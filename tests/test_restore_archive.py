import io
import os
import pathlib
import tarfile
import tempfile
import unittest

from scripts import restore_archive


class RestoreArchiveTests(unittest.TestCase):
    def make_archive(self, path, entries):
        with tarfile.open(path, "w:gz") as archive:
            for name, kind, content in entries:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    payload = content.encode()
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
                elif kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = content
                    archive.addfile(info)

    def valid_entries(self):
        return [
            ("opt/proxy-panel/data/config.json", "file", "{}"),
            ("etc/proxy-panel/install.conf", "file", "DOMAIN=proxy.example.com\n"),
            ("etc/nginx/sites-available/proxy-panel.conf", "file", "server {}\n"),
        ]

    def test_extracts_expected_regular_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = os.path.join(temporary, "backup.tar.gz")
            output = os.path.join(temporary, "out")
            self.make_archive(archive, self.valid_entries())
            restore_archive.extract_archive(archive, output)
            self.assertEqual(
                pathlib.Path(output, "opt/proxy-panel/data/config.json").read_text(),
                "{}",
            )

    def test_rejects_extra_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = os.path.join(temporary, "backup.tar.gz")
            entries = self.valid_entries() + [("../../etc/passwd", "file", "bad")]
            self.make_archive(archive, entries)
            with self.assertRaisesRegex(ValueError, "layout"):
                restore_archive.extract_archive(archive, os.path.join(temporary, "out"))

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = os.path.join(temporary, "backup.tar.gz")
            entries = self.valid_entries()
            entries[0] = (entries[0][0], "symlink", "/etc/passwd")
            self.make_archive(archive, entries)
            with self.assertRaisesRegex(ValueError, "member"):
                restore_archive.extract_archive(archive, os.path.join(temporary, "out"))


if __name__ == "__main__":
    unittest.main()
