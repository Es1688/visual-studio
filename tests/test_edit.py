"""Real-mode tests for edit_image + upscale (P3). All HTTP is mocked via
httpx.MockTransport — no network, no real FAL_KEY (AGENTS.md, «Секреты»)."""

import io
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import edit_image
import fal_adapter
import httpx
import pytest
import studio_common as sc
import upscale
from PIL import Image

REPO = Path(__file__).resolve().parent.parent


def tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (20, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


TINY_PNG = tiny_png()


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    """Temporary repo root: real config/ and schema/, empty demo project."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    (tmp_path / "projects" / "demo" / "references").mkdir(parents=True)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("FAL_KEY", "test-key")
    return tmp_path


def seed_source(root: Path, *, size=(64, 64), cost=0.029) -> str:
    """Source asset on disk + manifest entry; returns the asset id."""
    asset_id = "demo-img-0001"
    rel_path = f"projects/demo/generated/{asset_id}.png"
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 90, 150)).save(path, format="PNG")
    entry = {
        "id": asset_id,
        "path": rel_path,
        "type": "image",
        "provider": "fal",
        "model": "flux-dev",
        "api_version": None,
        "operation": "text_to_image",
        "prompt": "source",
        "seed": None,
        "params": {},
        "parents": [],
        "cost_usd": cost,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "qc": {"status": "pending", "checks": []},
    }
    with (root / "projects" / "demo" / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump({"project": "demo", "entries": [entry]}, f)
    return asset_id


def mock_fal():
    """MockTransport speaking the queue API; returns (transport, call counters).

    Records the submit endpoint path and JSON payload for assertions.
    """
    calls = {"submit": 0, "status": 0, "response": 0, "download": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["submit"] += 1
            calls["endpoint"] = request.url.path.lstrip("/")
            calls["payload"] = json.loads(request.content)
            prefix = f"https://queue.fal.run{request.url.path}/requests/req-1"
            return httpx.Response(
                200,
                json={
                    "request_id": "req-1",
                    "status_url": f"{prefix}/status",
                    "response_url": prefix,
                },
            )
        if request.url.path.endswith("/status"):
            calls["status"] += 1
            return httpx.Response(200, json={"status": "COMPLETED"})
        if "/requests/" in request.url.path:
            calls["response"] += 1
            return httpx.Response(
                200, json={"images": [{"url": "https://media.test/img-0.png"}]}
            )
        calls["download"] += 1
        return httpx.Response(200, content=TINY_PNG)

    return httpx.MockTransport(handler), calls


def make_client(transport: httpx.MockTransport) -> fal_adapter.FalClient:
    return fal_adapter.FalClient(transport=transport, backoff=0.0, poll_interval_s=0.0)


def http_calls(calls: dict) -> int:
    return sum(value for value in calls.values() if isinstance(value, int))


def run_edit(staged_root: Path, argv: list[str], client: fal_adapter.FalClient) -> int:
    return edit_image.main(
        ["--project", "demo", "--asset", "demo-img-0001", "--mode", "edit", *argv],
        client=client,
    )


def test_edit_mode_payload_file_and_manifest(staged):
    asset = seed_source(staged)
    transport, calls = mock_fal()
    rc = run_edit(
        staged, ["--prompt", "put the product on a marble table"], make_client(transport)
    )
    assert rc == 0

    assert calls["endpoint"] == "fal-ai/flux-pro/kontext"
    payload = calls["payload"]
    assert payload["prompt"] == "put the product on a marble table"
    assert payload["image_url"].startswith("data:image/png;base64,")
    assert "aspect_ratio" not in payload  # omitted -> fal match_input_image

    out = staged / "projects" / "demo" / "working" / "demo-img-0002.png"
    assert out.is_file()
    assert out.read_bytes() == TINY_PNG

    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    entry = manifest["entries"][1]
    assert entry["id"] == "demo-img-0002"
    assert entry["operation"] == "edit_reference"
    assert entry["model"] == "flux-pro-kontext"
    assert entry["parents"] == [asset]
    assert entry["cost_usd"] == pytest.approx(0.04)
    assert sc.spend_total("demo") == pytest.approx(0.029 + 0.04)


def test_edit_aspect_maps_to_kontext_ratio(staged):
    seed_source(staged)
    transport, calls = mock_fal()
    rc = run_edit(staged, ["--prompt", "x", "--aspect", "portrait_4_5"], make_client(transport))
    assert rc == 0
    expected = sc.find_model("flux-pro-kontext")["aspect_params"]["portrait_4_5"]
    assert calls["payload"]["aspect_ratio"] == expected  # "3:4" in capabilities


def test_edit_ref_is_ignored_with_warning(staged, capsys):
    seed_source(staged)
    transport, calls = mock_fal()
    rc = run_edit(
        staged,
        ["--prompt", "x", "--ref", "projects/demo/references/item.png"],
        make_client(transport),
    )
    assert rc == 0
    assert "ignoring --ref" in capsys.readouterr().err
    assert calls["submit"] == 1  # one request, no extra per-ref calls


def test_inpaint_without_mask_exits_2_before_http(staged):
    seed_source(staged)
    transport, calls = mock_fal()
    rc = edit_image.main(
        ["--project", "demo", "--asset", "demo-img-0001", "--mode", "inpaint",
         "--prompt", "add steam"],
        client=make_client(transport),
    )
    assert rc == 2
    assert http_calls(calls) == 0


def test_inpaint_payload_uses_mask_and_source_megapixels(staged):
    asset = seed_source(staged, size=(512, 512))
    mask_rel = "projects/demo/references/mask.png"
    Image.new("L", (512, 512), 255).save(staged / mask_rel, format="PNG")
    transport, calls = mock_fal()
    rc = edit_image.main(
        ["--project", "demo", "--asset", asset, "--mode", "inpaint",
         "--prompt", "add steam", "--mask", mask_rel],
        client=make_client(transport),
    )
    assert rc == 0

    assert calls["endpoint"] == "fal-ai/flux/dev/fill"
    payload = calls["payload"]
    assert payload["mask_url"].startswith("data:image/png;base64,")
    assert payload["image_size"] == {"width": 1080, "height": 1080}  # square default

    with Image.open(staged / "projects" / "demo" / "generated" / f"{asset}.png") as img:
        source_mp = img.size[0] * img.size[1] / 1e6
    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    entry = manifest["entries"][1]
    assert entry["operation"] == "inpaint"
    assert entry["parents"] == [asset]
    assert entry["cost_usd"] == pytest.approx(
        sc.estimate_cost("flux-fill", 1, megapixels=source_mp)
    )
    assert entry["params"]["mask"] == mask_rel


def test_remove_background_is_free(staged):
    asset = seed_source(staged)
    transport, calls = mock_fal()
    rc = edit_image.main(
        ["--project", "demo", "--asset", asset, "--mode", "remove_background"],
        client=make_client(transport),
    )
    assert rc == 0

    assert calls["endpoint"] == "fal-ai/birefnet/v2"
    payload = calls["payload"]
    assert set(payload) == {"image_url", "output_format"}  # prompt not required
    assert payload["output_format"] == "png"
    assert payload["image_url"].startswith("data:image/png;base64,")

    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    entry = manifest["entries"][1]
    assert entry["operation"] == "remove_background"
    assert entry["parents"] == [asset]
    assert entry["cost_usd"] == 0.0


def test_upscale_payload_and_cost(staged):
    asset = seed_source(staged, size=(512, 512))
    transport, calls = mock_fal()
    rc = upscale.main(
        ["--project", "demo", "--asset", asset],
        client=make_client(transport),
    )
    assert rc == 0

    assert calls["endpoint"] == "fal-ai/clarity-upscaler"
    payload = calls["payload"]
    assert payload["upscale_factor"] == 2
    assert payload["image_url"].startswith("data:image/png;base64,")

    with Image.open(staged / "projects" / "demo" / "generated" / f"{asset}.png") as img:
        source_mp = img.size[0] * img.size[1] / 1e6
    expected = sc.estimate_cost("esrgan", 1, megapixels=source_mp * 4)  # output MP
    assert expected == pytest.approx(0.0315)  # 512x512 -> 1024x1024 = 1.048576 MP

    out = staged / "projects" / "demo" / "working" / "demo-up-0002.png"
    assert out.read_bytes() == TINY_PNG
    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    entry = manifest["entries"][1]
    assert entry["operation"] == "upscale"
    assert entry["parents"] == [asset]
    assert entry["params"]["scale"] == 2
    assert entry["cost_usd"] == pytest.approx(expected)


def test_upscale_scale_out_of_range_exits_2(staged):
    seed_source(staged)
    transport, calls = mock_fal()
    rc = upscale.main(
        ["--project", "demo", "--asset", "demo-img-0001", "--scale", "9"],
        client=make_client(transport),
    )
    assert rc == 2
    assert http_calls(calls) == 0


def test_budget_exceeded_blocks_before_http(staged):
    seed_source(staged, cost=4.98)  # + 0.04 planned > per_day 5.00 (budget.yaml)
    transport, calls = mock_fal()
    rc = run_edit(staged, ["--prompt", "x"], make_client(transport))
    assert rc == 2
    assert http_calls(calls) == 0


def test_unknown_asset_exits_2(staged, capsys):
    transport, calls = mock_fal()
    rc = run_edit(staged, ["--prompt", "x"], make_client(transport))
    assert rc == 2
    assert "not found" in capsys.readouterr().err
    assert http_calls(calls) == 0


def test_asset_without_file_exits_2(staged):
    seed_source(staged)
    (staged / "projects" / "demo" / "generated" / "demo-img-0001.png").unlink()
    transport, calls = mock_fal()
    rc = run_edit(staged, ["--prompt", "x"], make_client(transport))
    assert rc == 2
    assert http_calls(calls) == 0


def test_missing_fal_key_exits_2(staged, monkeypatch, capsys):
    seed_source(staged)
    monkeypatch.delenv("FAL_KEY", raising=False)
    transport, calls = mock_fal()
    rc = edit_image.main(
        ["--project", "demo", "--asset", "demo-img-0001", "--mode", "edit",
         "--prompt", "x"]
    )
    assert rc == 2
    assert "FAL_KEY is not set" in capsys.readouterr().err
    assert http_calls(calls) == 0


def test_fal_error_exits_4(staged):
    seed_source(staged)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, text="bad request")
    )
    rc = run_edit(staged, ["--prompt", "x"], make_client(transport))
    assert rc == 4
