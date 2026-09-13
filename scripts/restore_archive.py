#!/usr/bin/env python3
import os
import pathlib
import sys
import tarfile


EXPECTED = {
    "opt/proxy-panel/data/config.json": 10 * 1024 * 1024,
    "etc/proxy-panel/install.conf": 64 * 1024,
    "etc/nginx/sites-available/proxy-panel.conf": 1024 * 1024,
}


def extract_archive(archive_path, destination):
    destination = pathlib.Path(destination).resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name.removeprefix("./") for member in members]
        if len(names) != len(EXPECTED) or set(names) != set(EXPECTED):
            raise ValueError("archive layout is invalid")
        for member, name in zip(members, names):
            if not member.isfile() or member.size > EXPECTED[name]:
                raise ValueError(f"invalid archive member: {name}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read archive member: {name}")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(target, flags, 0o600)
            with source, os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(64 * 1024):
                    output.write(chunk)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: restore_archive.py ARCHIVE DESTINATION")
    try:
        extract_archive(sys.argv[1], sys.argv[2])
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
