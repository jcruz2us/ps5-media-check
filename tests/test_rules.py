import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ps5_media_check import evaluate, FAIL, WARN


def vid(codec, profile="", pix="yuv420p", level=0, side=None, w=1920, h=1080):
    s = {"codec_type": "video", "codec_name": codec, "profile": profile, "pix_fmt": pix,
         "level": level, "width": w, "height": h}
    if side:
        s["side_data_list"] = side
    return s


AAC = {"codec_type": "audio", "codec_name": "aac"}
DTS = {"codec_type": "audio", "codec_name": "dts"}


def sev(streams, ext=".mkv"):
    return {s for s, _ in evaluate({"streams": streams}, ext)}


def test_h264_ok():
    assert sev([vid("h264", "High", level=41), AAC]) == set()


def test_hi10p_fails():
    assert FAIL in sev([vid("h264", "High 10", "yuv420p10le", 41), AAC])


def test_hevc_main10_ok():
    assert sev([vid("hevc", "Main 10", "yuv420p10le", 150), AAC]) == set()


def test_dv_profile5_fails():
    dv = [{"side_data_type": "DOVI configuration record", "dv_profile": 5, "dv_bl_signal_compatibility_id": 0}]
    assert FAIL in sev([vid("hevc", "Main 10", "yuv420p10le", 150, side=dv), AAC])


def test_dv_profile8_hdr10_fallback_ok():
    dv = [{"side_data_type": "DOVI configuration record", "dv_profile": 8, "dv_bl_signal_compatibility_id": 1}]
    assert sev([vid("hevc", "Main 10", "yuv420p10le", 150, side=dv), AAC]) == set()


def test_dv_profile7_warns():
    dv = [{"side_data_type": "DOVI configuration record", "dv_profile": 7, "dv_bl_signal_compatibility_id": 6}]
    assert sev([vid("hevc", "Main 10", "yuv420p10le", 150, side=dv), AAC]) == {WARN}


def test_dts_only_warns():
    assert sev([vid("h264", "High", level=41), DTS]) == {WARN}


def test_unsupported_codec_fails():
    assert FAIL in sev([vid("vc1"), AAC])


def test_no_video_fails():
    assert FAIL in sev([AAC])


def test_avi_warns():
    assert WARN in sev([vid("h264", "High", level=41), AAC], ".avi")
