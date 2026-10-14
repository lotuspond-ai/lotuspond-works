"""Fixed, value-free operational failures for local SDK tools."""

SDK_ERRORS = {
    "file_unreadable": "The input must be a readable regular file.",
    "input_too_large": "The input exceeds the byte limit.",
    "unsafe_private_key": "Private key material must be owner-only and owned by the current user.",
    "invalid_signature_envelope": "The detached signature envelope is invalid.",
    "invalid_trust_set": "The independent trust set is invalid.",
    "invalid_signing_key": "The input must be an unencrypted PKCS8 Ed25519 private key.",
    "unsupported_key_platform": "This platform cannot enforce safe private-key file operations.",
    "output_exists": "The output already exists and cannot be overwritten.",
    "file_unwritable": "The output could not be written completely.",
    "key_paths_conflict": "Private and public key output paths must differ.",
    "keypair_incomplete": "Key file publication is incomplete; operator inspection is required.",
    "output_incomplete": "Output publication is incomplete; operator inspection is required.",
    "report_too_large": "The report exceeds the supported byte limit.",
}


class SdkError(ValueError):
    def __init__(self, code: str):
        super().__init__(SDK_ERRORS[code])
        self.code = code
