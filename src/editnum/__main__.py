import sys


def _self_test() -> None:
    import editnum.app  # noqa: F401
    import onnxruntime  # noqa: F401
    import pikepdf  # noqa: F401
    import pymupdf  # noqa: F401
    import rapidocr  # noqa: F401


if "--self-test" in sys.argv:
    _self_test()
    raise SystemExit(0)


from editnum.app import run  # noqa: E402


if __name__ == "__main__":
    run()
