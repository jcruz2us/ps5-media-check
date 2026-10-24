#!/bin/sh
# ffprobe via Docker, for hosts without ffprobe.
# Mounts MEDIA_ROOT at the same path inside the container so file paths pass through unchanged.
# Usage: MEDIA_ROOT=/media ps5_media_check.py /media/Movies --ffprobe examples/ffprobe-docker.sh
# If docker needs elevated rights, set DOCKER="sudo -n docker".
: "${MEDIA_ROOT:?set MEDIA_ROOT to the library root}"
exec ${DOCKER:-docker} run --rm --entrypoint ffprobe -v "$MEDIA_ROOT:$MEDIA_ROOT:ro" \
  "${FFPROBE_IMAGE:-linuxserver/ffmpeg:latest}" "$@"
