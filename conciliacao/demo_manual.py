"""Create the small, manually inspectable demo in a new directory."""

import argparse
import shutil
import sqlite3
from pathlib import Path


def create_demo(destination):
    destination = Path(destination)
    source = Path(__file__).resolve().parent.parent / "examples" / "amostra_manual"
    # Refuse an existing destination to avoid replacing user data.
    destination.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(destination / "banco.db") as conn:
        conn.executescript((source / "banco.sql").read_text(encoding="utf-8"))
    shutil.copyfile(source / "bandeira.csv", destination / "bandeira.csv")
    return destination


def main():
    parser = argparse.ArgumentParser(description="Cria amostra sintetica em pasta nova")
    parser.add_argument("destino", help="pasta nova para banco.db e bandeira.csv")
    args = parser.parse_args()
    try:
        destination = create_demo(args.destino)
    except FileExistsError:
        parser.error("o destino ja existe; escolha uma pasta nova")
    print(f"Amostra criada em {destination}")


if __name__ == "__main__":
    main()
