import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ps5_media_check import evaluate, FAIL, WARN  # noqa: E402


def vid(codec, profile="", pix="yuv420p", level=0, side=None, w=1920, h=1080):
    s = {"codec_type": "video", "codec_name": codec, "profile": profile, "pix_fmt": pix,
         "level": level, "width": w, "height": h}
    if side:
        s["side_data_list"] = side
    return s


def dovi(profile, compat):
    return [{"side_data_type": "DOVI configuration record", "dv_profile": profile,
             "dv_bl_signal_compatibility_id": compat}]


AAC = {"codec_type": "audio", "codec_name": "aac"}
DTS = {"codec_type": "audio", "codec_name": "dts"}
TRUEHD = {"codec_type": "audio", "codec_name": "truehd"}
PGS_FORCED = {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle",
              "disposition": {"default": 0, "forced": 1}}
PGS_DEFAULT = {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle",
               "disposition": {"default": 1, "forced": 0}}
PGS_OPTIONAL = {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle",
                "disposition": {"default": 0, "forced": 0}}
H264 = vid("h264", "High", level=41)
HEVC10 = vid("hevc", "Main 10", "yuv420p10le", 150, w=3840, h=2160)


def sev(streams, ext=".mkv", target="plex"):
    return {s for s, _ in evaluate({"streams": streams}, ext, target)}


# --- plex target (default) ---------------------------------------------------

def test_h264_ok():
    assert sev([H264, AAC]) == set()


def test_hevc_main10_hdr10_4k_ok():
    assert sev([HEVC10, AAC]) == set()


def test_lossless_audio_is_not_a_plex_problem():
    # Plex transcodes TrueHD/DTS to AAC for the PS5 app; the video still direct-streams.
    assert sev([HEVC10, TRUEHD]) == set()
    assert sev([H264, DTS]) == set()


def test_avi_container_ok_on_plex():
    assert sev([H264, AAC], ".avi") == set()


def test_hi10p_fails():
    assert FAIL in sev([vid("h264", "High 10", "yuv420p10le", 41), AAC])


def test_hevc_rext_fails():
    assert FAIL in sev([vid("hevc", "Rext", "yuv444p10le", 150), AAC])


def test_dv_profile5_fails():
    assert FAIL in sev([vid("hevc", "Main 10", "yuv420p10le", 150, side=dovi(5, 0)), AAC])


def test_dv_profile8_with_hdr10_fallback_ok():
    assert sev([vid("hevc", "Main 10", "yuv420p10le", 150, side=dovi(8, 1)), AAC]) == set()
    assert sev([vid("hevc", "Main 10", "yuv420p10le", 150, side=dovi(8, 6)), AAC]) == set()


def test_dv_profile7_warns():
    assert sev([vid("hevc", "Main 10", "yuv420p10le", 150, side=dovi(7, 6)), AAC]) == {WARN}


def test_server_transcode_codecs_warn():
    for codec in ("av1", "vp9", "vc1", "mpeg4", "mpeg2video"):
        assert sev([vid(codec), AAC]) == {WARN}, codec


def test_unknown_codec_fails():
    assert FAIL in sev([vid("theora"), AAC])


def test_no_video_fails():
    assert FAIL in sev([AAC])


def test_forced_pgs_warns_optional_pgs_ok():
    assert sev([H264, AAC, PGS_FORCED]) == {WARN}
    assert sev([H264, AAC, PGS_OPTIONAL]) == set()
    assert sev([H264, AAC, PGS_DEFAULT]) == set()


def test_h264_level_above_52_warns():
    assert sev([vid("h264", "High", level=62), AAC]) == {WARN}


# --- media-gallery target (Sony's published list) ----------------------------

def test_gallery_mkv_h264_aac_ok():
    assert sev([H264, AAC], ".mkv", "media-gallery") == set()


def test_gallery_rejects_hevc():
    assert FAIL in sev([HEVC10, AAC], ".mkv", "media-gallery")


def test_gallery_mkv_level_limit_42():
    assert FAIL in sev([vid("h264", "High", level=51), AAC], ".mkv", "media-gallery")
    assert sev([vid("h264", "High", level=51), AAC], ".mp4", "media-gallery") == set()


def test_gallery_non_aac_audio_warns():
    assert sev([H264, DTS], ".mkv", "media-gallery") == {WARN}


def test_gallery_rejects_avi():
    assert FAIL in sev([H264, AAC], ".avi", "media-gallery")
