#!/bin/sh
# ffprobe via Docker, for hosts without ffprobe (e.g. Synology DSM, whose bundled ffmpeg lacks it).
# Mounts MEDIA_ROOT at the same path inside the container so file paths pass through unchanged.
# Usage: MEDIA_ROOT=/volume1/video ps5_media_check.py /volume1/video/Movies --ffprobe examples/ffprobe-docker.sh
# On Synology, docker needs root: set DOCKER="sudo -n /usr/local/bin/docker".
: "${MEDIA_ROOT:?set MEDIA_ROOT to the library root}"
exec ${DOCKER:-docker} run --rm --entrypoint ffprobe -v "$MEDIA_ROOT:$MEDIA_ROOT:ro" \
  "${FFPROBE_IMAGE:-linuxserver/ffmpeg:latest}" "$@"
