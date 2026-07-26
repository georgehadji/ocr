"""Generate synthetic Greek fixture pages for CER regression tests.

Each fixture is a PIL-rendered PNG with known ground-truth text.
Run from the repository root:

    python scripts/generate_fixtures.py

Output lands in tests/corpus/ as:
    {script}-{index}.png       — synthetic page image
    {script}-{index}.txt       — ground-truth text (NFC-normalized)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CORPUS = Path("tests/corpus")
FONT_SIZE = 28
MARGIN = 30

FIXTURES: list[dict[str, str]] = [
    {
        "id": "modern-1",
        "script": "modern",
        "text": (
            "Η Ελλάδα είναι μια χώρα της νοτιοανατολικής Ευρώπης.\n"
            "Βρίσκεται στο σταυροδρόμι της Ευρώπης, της Ασίας\n"
            "και της Αφρικής. Πρωτεύουσά της είναι η Αθήνα."
        ),
        "width": 780,
        "height": 180,
    },
    {
        "id": "polytonic-1",
        "script": "polytonic",
        "text": (
            "Ἡ Ἑλλὰς ἐστὶ χώρα τῆς νοτιοανατολικῆς Εὐρώπης,\n"
            "κειμένη ἐν τῷ σταυροδρομίῳ τῆς Εὐρώπης, τῆς Ἀσίας\n"
            "καὶ τῆς Ἀφρικῆς. Μητρόπολις δ᾽ αὐτῆς ἐστιν αἱ Ἀθῆναι."
        ),
        "width": 800,
        "height": 180,
    },
    {
        "id": "ancient-1",
        "script": "ancient",
        "text": (
            "τὸν δ᾽ ἀπαμειβόμενος προσέφη πόδας ὠκὺς Ἀχιλλεύς·\n"
            "Ἀτρεΐδη κύδιστε ἄναξ ἀνδρῶν Ἀγάμεμνον\n"
            "δῶρα μὲν οὐκέτ᾽ ἔγωγε τεὴν ὠίσομαι ἀγγελίην."
        ),
        "width": 800,
        "height": 180,
    },
]


def _draw_text(text: str, width: int, height: int, font_path: str = "arial.ttf") -> Image.Image:
    """Render text onto a white background and return a grayscale image."""
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, FONT_SIZE)
    y = MARGIN
    for line in text.split("\n"):
        draw.text((MARGIN, y), line, fill=0, font=font)
        bbox = draw.textbbox((MARGIN, y), line, font=font)
        y += bbox[3] - bbox[1] + 6
    return image


def main() -> None:
    font_paths = ["arial.ttf", "C:/Windows/Fonts/arial.ttf"]
    font_path = next((p for p in font_paths if Path(p).is_file()), "arial.ttf")

    CORPUS.mkdir(parents=True, exist_ok=True)

    for fixture in FIXTURES:
        identifier: str = fixture["id"]
        text: str = fixture["text"]
        width: int = fixture["width"]
        height: int = fixture["height"]

        img = _draw_text(text, width, height, font_path)
        png_path = CORPUS / f"{identifier}.png"
        img.save(png_path, "PNG")

        txt_path = CORPUS / f"{identifier}.txt"
        txt_path.write_text(text, encoding="utf-8")

        print(f"Created  {png_path}  ({img.width}x{img.height})")
        print(f"Created  {txt_path}")

    print(f"\n{len(FIXTURES)} fixture(s) written to {CORPUS}/")


if __name__ == "__main__":
    main()
