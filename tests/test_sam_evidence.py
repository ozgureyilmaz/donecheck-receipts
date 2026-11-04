from pathlib import Path

from PIL import Image

from receipts.auditor.sam_evidence import draw_overlay, parse_boxes


def test_parse_documented_sam_box_token_sample() -> None:
    text = "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|><|mask;x=0;y=0;data=27,60,~...|>"
    assert parse_boxes(text) == [{"x1": 211, "y1": 228, "x2": 270, "y2": 254, "w": 320, "h": 334}]


def test_box_confidence_is_optional() -> None:
    token = "<|box;x1=1;y1=2;x2=8;y2=9;w=10;h=12;c=0.97|>"
    assert parse_boxes(token) == [{"x1": 1, "y1": 2, "x2": 8, "y2": 9, "w": 10, "h": 12}]


def test_zero_matches_is_a_valid_empty_result() -> None:
    assert parse_boxes("<0f>0<|mask;x=0;y=0;data=27,60,~...|>") == []
    assert parse_boxes("SAM found no matching region") == []


def test_invalid_geometry_is_rejected_and_multiple_boxes_are_kept() -> None:
    text = "".join(
        [
            "<|box;x1=1;y1=2;x2=8;y2=9;w=10;h=12|>",
            "<|box;x1=1;y1=2;x2=11;y2=9;w=10;h=12|>",  # x2 exceeds image width
            "<|box;x1=8;y1=2;x2=7;y2=9;w=10;h=12|>",  # reversed horizontal bounds
            "<|box;x1=0;y1=0;x2=4;y2=5;w=8;h=10|>",
        ]
    )
    assert parse_boxes(text) == [
        {"x1": 1, "y1": 2, "x2": 8, "y2": 9, "w": 10, "h": 12},
        {"x1": 0, "y1": 0, "x2": 4, "y2": 5, "w": 8, "h": 10},
    ]


def test_draw_overlay_scales_frame_coordinates(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    target = tmp_path / "overlay.png"
    Image.new("RGB", (640, 400), "white").save(source)
    box = {"x1": 20, "y1": 10, "x2": 100, "y2": 50, "w": 320, "h": 200}

    draw_overlay(source, target, [box])

    with Image.open(target) as result:
        assert result.size == (640, 400)
        assert result.getpixel((40, 20)) == (244, 63, 94)
        assert result.getpixel((200, 100)) == (244, 63, 94)
        assert result.getpixel((120, 60)) == (255, 255, 255)
