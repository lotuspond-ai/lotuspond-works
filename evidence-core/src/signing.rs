//! Ed25519 publisher signatures over evidence manifests.
//!
//! A manifest proves which files an artifact holds; a signature proves who published that
//! manifest. The publisher signs the manifest file's exact bytes, prefixed with a context
//! line so the signature cannot be replayed as a signature over anything else:
//!
//! ```text
//! message = "lotuspond-evidence-manifest-v1\n" || manifest bytes
//! ```
//!
//! Keys are standard PKCS#8 (private) and SubjectPublicKeyInfo (public) PEM, so a verifier
//! can also check a signature with OpenSSL, independently of this tool. Trust comes only from
//! the public key a verifier obtains out of band: the key identifier in a signature file
//! names the key that made it, but never makes that key trusted.

use crate::{EvidenceError, Manifest, hex, parse_manifest, verify_manifest_bytes};
use ed25519_dalek::pkcs8::spki::der::pem::LineEnding;
use ed25519_dalek::pkcs8::{DecodePrivateKey, DecodePublicKey, EncodePrivateKey, EncodePublicKey};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::Path;
use zeroize::Zeroizing;

pub const SIGNATURE_SCHEMA_VERSION: u32 = 1;
pub const SIGNATURE_ALGORITHM: &str = "ed25519";
pub const CONTEXT: &str = "lotuspond-evidence-manifest-v1";

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManifestSignature {
    pub schema_version: u32,
    pub algorithm: String,
    pub context: String,
    /// `sha256:` and the SHA-256 of the raw 32-byte public key, for choosing a key and for
    /// a clear refusal when the wrong one is supplied.
    pub key_id: String,
    /// SHA-256 of the signed manifest bytes, so a signature for another manifest is named.
    pub manifest_sha256: String,
    /// The 64-byte Ed25519 signature in lowercase hex.
    pub signature: String,
}

/// The message a publisher signs for a manifest's exact bytes.
pub fn signed_message(manifest_bytes: &[u8]) -> Vec<u8> {
    let mut message = Vec::with_capacity(CONTEXT.len() + 1 + manifest_bytes.len());
    message.extend_from_slice(CONTEXT.as_bytes());
    message.push(b'\n');
    message.extend_from_slice(manifest_bytes);
    message
}

pub fn key_id(key: &VerifyingKey) -> String {
    format!("sha256:{}", hex(&Sha256::digest(key.to_bytes())))
}

fn invalid(message: impl Into<String>) -> EvidenceError {
    EvidenceError::Signing(message.into())
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

fn decode_hex<const N: usize>(value: &str) -> Option<[u8; N]> {
    if !is_lower_hex(value, N * 2) {
        return None;
    }
    let mut bytes = [0_u8; N];
    for (index, pair) in value.as_bytes().chunks(2).enumerate() {
        let text = std::str::from_utf8(pair).ok()?;
        bytes[index] = u8::from_str_radix(text, 16).ok()?;
    }
    Some(bytes)
}

/// Creates a key pair. The private key is written owner-only (0600) and neither file may
/// already exist, so a key is never silently replaced.
pub fn generate_keypair(private_path: &Path, public_path: &Path) -> Result<String, EvidenceError> {
    for path in [private_path, public_path] {
        if fs::symlink_metadata(path).is_ok() {
            return Err(invalid(format!("refusing to overwrite {}", path.display())));
        }
    }
    let mut seed = Zeroizing::new([0_u8; 32]);
    getrandom::fill(seed.as_mut())
        .map_err(|error| invalid(format!("no system randomness: {error}")))?;
    let signing_key = SigningKey::from_bytes(&seed);
    let private_pem = signing_key
        .to_pkcs8_pem(LineEnding::LF)
        .map_err(|error| invalid(format!("private key encoding failed: {error}")))?;
    let public_pem = signing_key
        .verifying_key()
        .to_public_key_pem(LineEnding::LF)
        .map_err(|error| invalid(format!("public key encoding failed: {error}")))?;

    write_new(private_path, private_pem.as_bytes(), true)?;
    if let Err(error) = write_new(public_path, public_pem.as_bytes(), false) {
        // Leave no half-made pair behind.
        let _ = fs::remove_file(private_path);
        return Err(error);
    }
    Ok(key_id(&signing_key.verifying_key()))
}

fn write_new(path: &Path, contents: &[u8], private: bool) -> Result<(), EvidenceError> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(if private { 0o600 } else { 0o644 });
    }
    #[cfg(not(unix))]
    let _ = private;
    let mut file = options.open(path)?;
    file.write_all(contents)?;
    file.sync_all()?;
    Ok(())
}

fn read_signing_key(path: &Path) -> Result<SigningKey, EvidenceError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(path)?.permissions().mode();
        if mode & 0o077 != 0 {
            return Err(invalid(format!(
                "{} is readable by other users; restrict it to its owner (chmod 600)",
                path.display()
            )));
        }
    }
    let pem = Zeroizing::new(fs::read_to_string(path)?);
    SigningKey::from_pkcs8_pem(&pem).map_err(|_| {
        invalid(format!(
            "{} is not a PKCS#8 Ed25519 private key",
            path.display()
        ))
    })
}

pub fn read_verifying_key(path: &Path) -> Result<VerifyingKey, EvidenceError> {
    VerifyingKey::from_public_key_pem(&fs::read_to_string(path)?).map_err(|_| {
        invalid(format!(
            "{} is not an Ed25519 SubjectPublicKeyInfo public key",
            path.display()
        ))
    })
}

/// Signs a manifest's exact bytes after checking it is a well-formed manifest, and writes
/// the signature file. The signature file may not already exist.
pub fn sign_manifest(
    manifest_path: &Path,
    private_key_path: &Path,
    signature_path: &Path,
) -> Result<ManifestSignature, EvidenceError> {
    let manifest_bytes = fs::read(manifest_path)?;
    parse_manifest(&manifest_bytes)?;
    let signing_key = read_signing_key(private_key_path)?;
    let signature = signing_key.sign(&signed_message(&manifest_bytes));
    let record = ManifestSignature {
        schema_version: SIGNATURE_SCHEMA_VERSION,
        algorithm: SIGNATURE_ALGORITHM.to_owned(),
        context: CONTEXT.to_owned(),
        key_id: key_id(&signing_key.verifying_key()),
        manifest_sha256: hex(&Sha256::digest(&manifest_bytes)),
        signature: hex(&signature.to_bytes()),
    };
    let mut encoded = serde_json::to_vec_pretty(&record)?;
    encoded.push(b'\n');
    write_new(signature_path, &encoded, false)?;
    Ok(record)
}

/// Checks the signature against a trusted public key, then checks the very bytes it
/// verified against the artifact directory. Returns the manifest and the signer's key id.
pub fn verify_signed_manifest(
    root: &Path,
    manifest_path: &Path,
    signature_path: &Path,
    public_key_path: &Path,
) -> Result<(Manifest, String), EvidenceError> {
    let trusted = read_verifying_key(public_key_path)?;
    let record: ManifestSignature = serde_json::from_reader(File::open(signature_path)?)?;
    if record.schema_version != SIGNATURE_SCHEMA_VERSION {
        return Err(invalid(format!(
            "unsupported signature schema_version {}",
            record.schema_version
        )));
    }
    if record.algorithm != SIGNATURE_ALGORITHM || record.context != CONTEXT {
        return Err(invalid(format!(
            "unsupported signature algorithm {} or context {}",
            record.algorithm, record.context
        )));
    }
    let trusted_id = key_id(&trusted);
    if record.key_id != trusted_id {
        return Err(EvidenceError::Verification(vec![format!(
            "signed by key {}, not the trusted key {trusted_id}",
            record.key_id
        )]));
    }
    let signature = decode_hex::<64>(&record.signature)
        .map(|bytes| Signature::from_bytes(&bytes))
        .ok_or_else(|| invalid("signature must be 128 lowercase hex characters"))?;

    let manifest_bytes = fs::read(manifest_path)?;
    let manifest_sha256 = hex(&Sha256::digest(&manifest_bytes));
    if !is_lower_hex(&record.manifest_sha256, 64) || record.manifest_sha256 != manifest_sha256 {
        return Err(EvidenceError::Verification(vec![format!(
            "the signature is for manifest {}, not this manifest {manifest_sha256}",
            record.manifest_sha256
        )]));
    }
    // Strict verification also refuses small-order keys and malleable signatures.
    trusted
        .verify_strict(&signed_message(&manifest_bytes), &signature)
        .map_err(|_| {
            EvidenceError::Verification(vec![
                "the signature does not verify with the trusted key".to_owned(),
            ])
        })?;
    let manifest = verify_manifest_bytes(root, manifest_path, &manifest_bytes)?;
    Ok((manifest, trusted_id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::create_manifest;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static SEQUENCE: AtomicU64 = AtomicU64::new(0);

    /// RFC 8032 section 7.1, TEST 1.
    const RFC_SECRET: &str = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60";
    const RFC_PUBLIC: &str = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a";
    const RFC_SIGNATURE: &str = "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b";

    /// The RFC key as the PKCS#8 PEM OpenSSL 3.6.2 writes for it.
    const RFC_PRIVATE_PEM: &str = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIJ1hsZ3v/VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g\n-----END PRIVATE KEY-----\n";
    /// Its public key, as `openssl pkey -pubout` writes it.
    const RFC_PUBLIC_PEM: &str = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=\n-----END PUBLIC KEY-----\n";
    /// OpenSSL 3.6.2's `pkeyutl -sign -rawin` of the context line and `FIXED_MANIFEST` with
    /// the RFC key, so the framing is checked against an independent implementation.
    const FIXED_MANIFEST: &str =
        "{\n  \"schema_version\": 1,\n  \"algorithm\": \"sha256\",\n  \"files\": []\n}\n";
    const OPENSSL_SIGNATURE: &str = "2f52545832a992c7add206da229ed73aa0714a1e7e983ed2fabaf1c2b8907a494eed401ad27929634819500ce18b8145fbe53abeca2146446f0588e529b7a40b";

    fn directory() -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "lotuspond-signing-{}-{suffix}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(root.join("artifacts")).unwrap();
        fs::write(
            root.join("artifacts/report.json"),
            b"{\"verdict\":\"pass\"}",
        )
        .unwrap();
        root
    }

    struct Published {
        root: PathBuf,
        artifacts: PathBuf,
        manifest: PathBuf,
        signature: PathBuf,
        public: PathBuf,
        private: PathBuf,
    }

    fn publish() -> Published {
        let root = directory();
        let artifacts = root.join("artifacts");
        let manifest = root.join("manifest.json");
        let (private, public) = (root.join("publisher.key"), root.join("publisher.pub"));
        create_manifest(&artifacts, &manifest).unwrap();
        generate_keypair(&private, &public).unwrap();
        let signature = root.join("manifest.sig.json");
        sign_manifest(&manifest, &private, &signature).unwrap();
        Published {
            root,
            artifacts,
            manifest,
            signature,
            public,
            private,
        }
    }

    #[test]
    fn the_library_produces_rfc_8032_signatures() {
        let secret = decode_hex::<32>(RFC_SECRET).unwrap();
        let key = SigningKey::from_bytes(&secret);
        assert_eq!(hex(&key.verifying_key().to_bytes()), RFC_PUBLIC);
        assert_eq!(hex(&key.sign(b"").to_bytes()), RFC_SIGNATURE);
    }

    #[test]
    fn keys_and_framing_match_openssl() {
        let key = SigningKey::from_pkcs8_pem(RFC_PRIVATE_PEM).unwrap();
        assert_eq!(hex(&key.to_bytes()), RFC_SECRET);
        let public = VerifyingKey::from_public_key_pem(RFC_PUBLIC_PEM).unwrap();
        assert_eq!(public, key.verifying_key());
        assert_eq!(
            key.verifying_key()
                .to_public_key_pem(LineEnding::LF)
                .unwrap(),
            RFC_PUBLIC_PEM
        );
        let signature = key.sign(&signed_message(FIXED_MANIFEST.as_bytes()));
        assert_eq!(hex(&signature.to_bytes()), OPENSSL_SIGNATURE);
    }

    #[test]
    fn a_signed_manifest_verifies_with_its_publisher_key() {
        let published = publish();
        let (manifest, signer) = verify_signed_manifest(
            &published.artifacts,
            &published.manifest,
            &published.signature,
            &published.public,
        )
        .unwrap();
        assert_eq!(manifest.files.len(), 1);
        assert_eq!(
            signer,
            key_id(&read_verifying_key(&published.public).unwrap())
        );
        fs::remove_dir_all(published.root).unwrap();
    }

    #[test]
    fn a_changed_manifest_signature_or_artifact_is_refused() {
        let published = publish();
        let verify = |p: &Published| {
            verify_signed_manifest(&p.artifacts, &p.manifest, &p.signature, &p.public)
                .unwrap_err()
                .to_string()
        };

        // Rewriting the manifest after signing, even to describe the files correctly.
        let original = fs::read(&published.manifest).unwrap();
        fs::write(&published.manifest, [original.as_slice(), b" "].concat()).unwrap();
        assert!(verify(&published).contains("not this manifest"));
        fs::write(&published.manifest, &original).unwrap();

        // A signature byte flipped while the rest of the record stays consistent.
        let text = fs::read_to_string(&published.signature).unwrap();
        let mut record: ManifestSignature = serde_json::from_str(&text).unwrap();
        let flipped = if record.signature.starts_with('0') {
            "1"
        } else {
            "0"
        };
        record.signature.replace_range(0..1, flipped);
        fs::write(&published.signature, serde_json::to_vec(&record).unwrap()).unwrap();
        assert!(verify(&published).contains("does not verify"));
        fs::write(&published.signature, text).unwrap();

        // A changed artifact: the signature holds, the manifest no longer matches the files.
        fs::write(
            published.artifacts.join("report.json"),
            b"{\"verdict\":\"block\"}",
        )
        .unwrap();
        assert!(verify(&published).contains("report.json"));
        fs::remove_dir_all(published.root).unwrap();
    }

    #[test]
    fn another_publishers_key_is_named_and_refused() {
        let published = publish();
        let (other_private, other_public) = (
            published.root.join("other.key"),
            published.root.join("other.pub"),
        );
        let other = generate_keypair(&other_private, &other_public).unwrap();
        let error = verify_signed_manifest(
            &published.artifacts,
            &published.manifest,
            &published.signature,
            &other_public,
        )
        .unwrap_err()
        .to_string();
        assert!(error.contains(&format!("not the trusted key {other}")));
        fs::remove_dir_all(published.root).unwrap();
    }

    #[test]
    fn unknown_or_unsupported_signature_records_are_refused() {
        let published = publish();
        let text = fs::read_to_string(&published.signature).unwrap();
        for (from, to) in [
            ("\"algorithm\": \"ed25519\"", "\"algorithm\": \"ed448\""),
            (
                "\"context\": \"lotuspond-evidence-manifest-v1\"",
                "\"context\": \"other\"",
            ),
            ("\"schema_version\": 1", "\"schema_version\": 2"),
            (
                "\"schema_version\": 1",
                "\"schema_version\": 1, \"note\": \"x\"",
            ),
        ] {
            fs::write(&published.signature, text.replace(from, to)).unwrap();
            assert!(
                verify_signed_manifest(
                    &published.artifacts,
                    &published.manifest,
                    &published.signature,
                    &published.public,
                )
                .is_err(),
                "{to} was accepted"
            );
        }
        fs::remove_dir_all(published.root).unwrap();
    }

    #[test]
    fn keys_are_never_overwritten_and_a_shared_private_key_is_refused() {
        let published = publish();
        let error = generate_keypair(&published.private, &published.root.join("new.pub"))
            .unwrap_err()
            .to_string();
        assert!(error.contains("refusing to overwrite"));
        assert!(!published.root.join("new.pub").exists());
        let error = sign_manifest(
            &published.manifest,
            &published.private,
            &published.signature,
        )
        .unwrap_err()
        .to_string();
        assert!(error.contains("exists"), "{error}");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&published.private)
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(mode & 0o777, 0o600);
            fs::set_permissions(&published.private, fs::Permissions::from_mode(0o644)).unwrap();
            let error = sign_manifest(
                &published.manifest,
                &published.private,
                &published.root.join("again.sig.json"),
            )
            .unwrap_err()
            .to_string();
            assert!(error.contains("readable by other users"));
        }
        fs::remove_dir_all(published.root).unwrap();
    }
}
