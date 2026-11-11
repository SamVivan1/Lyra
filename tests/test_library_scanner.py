"""Tests for LibraryScanner metadata extraction, in particular recording_mbid.

The recording MBID is never exposed through mutagen's EasyID3 view
(`musicbrainz_recordingid` is not a valid EasyID3 key), so it must be read
through the non-easy API.  All audio files here are fakes: no network, no
real tagged audio, no database file in the working directory.
"""

import pytest

from lyra.database import library_scanner


class _FakeAudio(dict):
    """Stand-in for a mutagen file object: a mapping of tag -> value."""


class _FakeUFID:
    """Stand-in for an ID3 UFID frame: the payload lives in `.data` as bytes."""

    def __init__(self, data):
        self.data = data


class _FakeTXXX:
    """Stand-in for a real ID3 TXXX frame: the payload lives in `.text`."""

    def __init__(self, text):
        self.text = text


class _FakeFileWithTags:
    """Stand-in for a real mutagen file object: tags live under `.tags`."""

    def __init__(self, tags):
        self.tags = tags


class _NoDB:
    """Truthy placeholder so LibraryScanner never opens the real database."""


@pytest.fixture
def scanner():
    return library_scanner.LibraryScanner("/nonexistent-library", db_connection=_NoDB())


def _patch_file(monkeypatch, audio):
    """Patch mutagen.File (non-easy) and record the keyword arguments used."""
    calls = []

    def fake_file(path, **kwargs):
        calls.append(kwargs)
        return audio

    monkeypatch.setattr(library_scanner.mutagen, "File", fake_file)
    return calls


class TestReadRecordingMbid:
    def test_reads_mbid_from_list_tag(self, monkeypatch):
        calls = _patch_file(
            monkeypatch,
            _FakeAudio({"musicbrainz_trackid": ["3f2b1c9e-0000-4000-8000-000000000000"]}),
        )

        assert (
            library_scanner.read_recording_mbid("x.mp3")
            == "3f2b1c9e-0000-4000-8000-000000000000"
        )
        # The bug was reading this tag through the easy API; it must not be.
        assert not any(call.get("easy") for call in calls)

    def test_reads_mbid_from_plain_string_tag(self, monkeypatch):
        _patch_file(
            monkeypatch,
            _FakeAudio({"musicbrainz_trackid": "4a1c2d3e-1111-4222-8333-444444444444"}),
        )

        assert (
            library_scanner.read_recording_mbid("x.flac")
            == "4a1c2d3e-1111-4222-8333-444444444444"
        )

    def test_reads_mbid_from_ufid_frame_data_bytes(self, monkeypatch):
        _patch_file(
            monkeypatch,
            _FakeAudio(
                {
                    "UFID:http://musicbrainz.org": _FakeUFID(
                        b"5b2d3e4f-2222-4333-8444-555555555555"
                    )
                }
            ),
        )

        assert (
            library_scanner.read_recording_mbid("x.mp3")
            == "5b2d3e4f-2222-4333-8444-555555555555"
        )

    def test_reads_mbid_from_txxx_frame_string(self, monkeypatch):
        _patch_file(
            monkeypatch,
            _FakeAudio(
                {"TXXX:MusicBrainz Recording Id": ["6c3e4f5a-3333-4444-8555-666666666666"]}
            ),
        )

        assert (
            library_scanner.read_recording_mbid("x.mp3")
            == "6c3e4f5a-3333-4444-8555-666666666666"
        )

    def test_reads_mbid_from_real_file_object_tags(self, monkeypatch):
        _patch_file(
            monkeypatch,
            _FakeFileWithTags(
                _FakeAudio({"musicbrainz_trackid": ["9e7a8b9c-6666-4777-8888-999999999999"]})
            ),
        )

        assert (
            library_scanner.read_recording_mbid("x.flac")
            == "9e7a8b9c-6666-4777-8888-999999999999"
        )

    def test_reads_mbid_from_txxx_frame_object(self, monkeypatch):
        # On a real MP3 mutagen hands back the TXXX frame itself, not a string.
        _patch_file(
            monkeypatch,
            _FakeFileWithTags(
                _FakeAudio(
                    {
                        "TXXX:MusicBrainz Recording Id": _FakeTXXX(
                            ["8f5a6b7c-5555-4666-8777-888888888888"]
                        )
                    }
                )
            ),
        )

        assert (
            library_scanner.read_recording_mbid("x.mp3")
            == "8f5a6b7c-5555-4666-8777-888888888888"
        )

    def test_returns_none_when_tag_missing(self, monkeypatch):
        _patch_file(monkeypatch, _FakeAudio({}))

        assert library_scanner.read_recording_mbid("x.mp3") is None

    def test_prefers_trackid_over_txxx_and_ufid(self, monkeypatch):
        _patch_file(
            monkeypatch,
            _FakeAudio(
                {
                    "musicbrainz_trackid": ["first-choice"],
                    "TXXX:MusicBrainz Recording Id": ["second-choice"],
                    "UFID:http://musicbrainz.org": _FakeUFID(b"third-choice"),
                }
            ),
        )

        assert library_scanner.read_recording_mbid("x.mp3") == "first-choice"

    def test_prefers_txxx_over_ufid(self, monkeypatch):
        _patch_file(
            monkeypatch,
            _FakeAudio(
                {
                    "TXXX:MusicBrainz Recording Id": ["second-choice"],
                    "UFID:http://musicbrainz.org": _FakeUFID(b"third-choice"),
                }
            ),
        )

        assert library_scanner.read_recording_mbid("x.mp3") == "second-choice"

    def test_strips_whitespace_and_treats_empty_as_none(self, monkeypatch):
        _patch_file(monkeypatch, _FakeAudio({"musicbrainz_trackid": ["   "]}))

        assert library_scanner.read_recording_mbid("x.mp3") is None

        _patch_file(monkeypatch, _FakeAudio({"musicbrainz_trackid": ["  abc  "]}))

        assert library_scanner.read_recording_mbid("x.mp3") == "abc"

    def test_returns_none_for_unsupported_value_type(self, monkeypatch):
        _patch_file(monkeypatch, _FakeAudio({"musicbrainz_trackid": 12345}))

        assert library_scanner.read_recording_mbid("x.mp3") is None

    def test_returns_none_when_tags_cannot_be_read(self, monkeypatch):
        def boom(path, **kwargs):
            raise OSError("corrupt file")

        monkeypatch.setattr(library_scanner.mutagen, "File", boom)

        assert library_scanner.read_recording_mbid("x.mp3") is None


class TestExtractMetadata:
    def test_mbid_lands_in_returned_metadata(self, monkeypatch, scanner):
        easy = _FakeAudio(
            {"artist": ["Sample Artist"], "title": ["Example Track"], "album": ["Sample Album"]}
        )
        full = _FakeAudio(
            {"musicbrainz_trackid": ["7d4f5a6b-4444-4555-8666-777777777777"]}
        )
        monkeypatch.setattr(library_scanner, "MutagenFile", lambda *a, **k: easy)
        monkeypatch.setattr(library_scanner.mutagen, "File", lambda *a, **k: full)

        metadata = scanner._extract_metadata("/nonexistent-library/A/Al/T.mp3")

        assert metadata == {
            "artist": "Sample Artist",
            "title": "Example Track",
            "album": "Sample Album",
            "recording_mbid": "7d4f5a6b-4444-4555-8666-777777777777",
        }
