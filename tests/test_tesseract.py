from omniocr.infrastructure.tesseract import TesseractEngine


def test_tesseract_output_parser_skips_non_word_and_invalid_rows() -> None:
    blocks = TesseractEngine(language="ell").parse_output(
        {
            "level": [4, 5, 5, 5],
            "text": ["", "hello", "ignored", "world"],
            "conf": ["-1", "92.5", "-1", "101"],
            "left": [0, 10, 20, 30],
            "top": [0, 20, 20, 20],
            "width": [0, 40, 20, 30],
            "height": [0, 10, 10, 12],
        }
    )

    assert len(blocks) == 1
    assert blocks[0].text == "hello"
    assert blocks[0].confidence.value == 92.5
    assert blocks[0].provenance is not None
    assert blocks[0].provenance.model_ref.model_name == "ell"
