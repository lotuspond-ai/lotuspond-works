"""Fixed, value-free operational failures for local SDK tools."""

SDK_ERRORS = {
    "file_unreadable": "The input must be a readable regular file.",
    "input_too_large": "The input exceeds the byte limit.",
    "unsafe_private_key": "Private key material must be owner-only and owned by the current user.",
    "report_too_large": "The report exceeds the supported byte limit.",
}


class SdkError(ValueError):
    def __init__(self, code: str):
        super().__init__(SDK_ERRORS[code])
        self.code = code
