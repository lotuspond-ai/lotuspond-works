use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fmt::{Display, Formatter};
use std::fs::{self, File};
use std::io::{self, BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};

pub mod signing;

pub const SCHEMA_VERSION: u32 = 1;
pub const ALGORITHM: &str = "sha256";

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    pub algorithm: String,
    pub files: Vec<ManifestFile>,
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct ManifestFile {
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Debug)]
pub enum EvidenceError {
    Io(io::Error),
    Json(serde_json::Error),
    Invalid(String),
    /// A key or signature record that cannot be used, as distinct from a manifest problem.
    Signing(String),
    Verification(Vec<String>),
}

impl Display for EvidenceError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(f, "I/O error: {error}"),
            Self::Json(error) => write!(f, "manifest JSON error: {error}"),
            Self::Invalid(message) => write!(f, "invalid manifest input: {message}"),
            Self::Signing(message) => write!(f, "signing input rejected: {message}"),
            Self::Verification(messages) => {
                write!(f, "verification failed: {}", messages.join("; "))
            }
        }
    }
}

impl std::error::Error for EvidenceError {}

impl From<io::Error> for EvidenceError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<serde_json::Error> for EvidenceError {
    fn from(value: serde_json::Error) -> Self {
        Self::Json(value)
    }
}

pub fn create_manifest(root: &Path, output: &Path) -> Result<Manifest, EvidenceError> {
    let root = canonical_directory(root)?;
    let output_absolute = absolute_without_resolving(output)?;
    let excluded = BTreeSet::from([output_absolute]);
    let manifest = Manifest {
        schema_version: SCHEMA_VERSION,
        algorithm: ALGORITHM.to_owned(),
        files: collect_files(&root, &excluded)?,
    };
    write_manifest(output, &manifest)?;
    Ok(manifest)
}

pub fn verify_manifest(root: &Path, manifest_path: &Path) -> Result<Manifest, EvidenceError> {
    verify_manifest_bytes(root, manifest_path, &fs::read(manifest_path)?)
}

/// Verifies `manifest_bytes`, read from `manifest_path`, against the files under `root`.
/// Signature verification passes the exact bytes it checked, so what was signed is what is
/// compared with the directory, however the file changes afterwards.
pub(crate) fn verify_manifest_bytes(
    root: &Path,
    manifest_path: &Path,
    manifest_bytes: &[u8],
) -> Result<Manifest, EvidenceError> {
    let root = canonical_directory(root)?;
    let manifest = parse_manifest(manifest_bytes)?;

    let manifest_absolute = absolute_without_resolving(manifest_path)?;
    let actual = collect_files(&root, &BTreeSet::from([manifest_absolute]))?;
    if actual == manifest.files {
        return Ok(manifest);
    }

    let expected: BTreeSet<_> = manifest.files.iter().collect();
    let observed: BTreeSet<_> = actual.iter().collect();
    let mut differences = Vec::new();
    for file in expected.difference(&observed) {
        differences.push(format!("missing or changed: {}", file.path));
    }
    for file in observed.difference(&expected) {
        differences.push(format!("unexpected or changed: {}", file.path));
    }
    Err(EvidenceError::Verification(differences))
}

pub(crate) fn parse_manifest(bytes: &[u8]) -> Result<Manifest, EvidenceError> {
    let manifest: Manifest = serde_json::from_slice(bytes)?;
    validate_manifest(&manifest)?;
    Ok(manifest)
}

fn validate_manifest(manifest: &Manifest) -> Result<(), EvidenceError> {
    if manifest.schema_version != SCHEMA_VERSION {
        return Err(EvidenceError::Invalid(format!(
            "unsupported schema_version {}",
            manifest.schema_version
        )));
    }
    if manifest.algorithm != ALGORITHM {
        return Err(EvidenceError::Invalid(format!(
            "unsupported algorithm {}",
            manifest.algorithm
        )));
    }
    let mut previous: Option<&str> = None;
    for file in &manifest.files {
        validate_relative_path(&file.path)?;
        if file.sha256.len() != 64
            || !file
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        {
            return Err(EvidenceError::Invalid(format!(
                "invalid SHA-256 for {}",
                file.path
            )));
        }
        if previous.is_some_and(|value| value >= file.path.as_str()) {
            return Err(EvidenceError::Invalid(
                "file entries must be strictly sorted with no duplicates".to_owned(),
            ));
        }
        previous = Some(&file.path);
    }
    Ok(())
}

fn collect_files(
    root: &Path,
    excluded: &BTreeSet<PathBuf>,
) -> Result<Vec<ManifestFile>, EvidenceError> {
    let mut paths = Vec::new();
    visit(root, root, excluded, &mut paths)?;
    paths.sort();
    paths
        .into_iter()
        .map(|path| {
            let metadata = fs::metadata(&path)?;
            let relative = path.strip_prefix(root).map_err(|_| {
                EvidenceError::Invalid(format!("path escaped root: {}", path.display()))
            })?;
            Ok(ManifestFile {
                path: portable_path(relative)?,
                size: metadata.len(),
                sha256: hash_file(&path)?,
            })
        })
        .collect()
}

fn visit(
    root: &Path,
    directory: &Path,
    excluded: &BTreeSet<PathBuf>,
    files: &mut Vec<PathBuf>,
) -> Result<(), EvidenceError> {
    let mut entries = fs::read_dir(directory)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_symlink() {
            return Err(EvidenceError::Invalid(format!(
                "symbolic links are not allowed: {}",
                path.display()
            )));
        }
        if file_type.is_dir() {
            visit(root, &path, excluded, files)?;
        } else if file_type.is_file() {
            let absolute = absolute_without_resolving(&path)?;
            if !excluded.contains(&absolute) {
                if !absolute.starts_with(root) {
                    return Err(EvidenceError::Invalid(format!(
                        "file escaped root: {}",
                        path.display()
                    )));
                }
                files.push(absolute);
            }
        } else {
            return Err(EvidenceError::Invalid(format!(
                "unsupported file type: {}",
                path.display()
            )));
        }
    }
    Ok(())
}

fn canonical_directory(path: &Path) -> Result<PathBuf, EvidenceError> {
    let canonical = fs::canonicalize(path)?;
    if !canonical.is_dir() {
        return Err(EvidenceError::Invalid(format!(
            "root is not a directory: {}",
            path.display()
        )));
    }
    Ok(canonical)
}

fn absolute_without_resolving(path: &Path) -> Result<PathBuf, EvidenceError> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let parent = absolute
        .parent()
        .ok_or_else(|| EvidenceError::Invalid("path has no parent".to_owned()))?;
    let name = absolute
        .file_name()
        .ok_or_else(|| EvidenceError::Invalid("path has no filename".to_owned()))?;
    Ok(fs::canonicalize(parent)?.join(name))
}

fn validate_relative_path(value: &str) -> Result<(), EvidenceError> {
    let path = Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(EvidenceError::Invalid(format!(
            "unsafe relative path: {value}"
        )));
    }
    Ok(())
}

fn portable_path(path: &Path) -> Result<String, EvidenceError> {
    let mut parts = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => parts.push(value.to_str().ok_or_else(|| {
                EvidenceError::Invalid(format!("path is not UTF-8: {}", path.display()))
            })?),
            _ => {
                return Err(EvidenceError::Invalid(format!(
                    "unsafe path: {}",
                    path.display()
                )));
            }
        }
    }
    Ok(parts.join("/"))
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut encoded, "{byte:02x}").expect("writing to a String cannot fail");
    }
    encoded
}

fn hash_file(path: &Path) -> Result<String, EvidenceError> {
    let mut reader = BufReader::new(File::open(path)?);
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(hex(&hasher.finalize()))
}

fn write_manifest(path: &Path, manifest: &Manifest) -> Result<(), EvidenceError> {
    let parent = path
        .parent()
        .ok_or_else(|| EvidenceError::Invalid("output has no parent directory".to_owned()))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("manifest")
    ));
    let mut file = File::create(&temporary)?;
    serde_json::to_writer_pretty(&mut file, manifest)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static FIXTURE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

    fn fixture() -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "lotuspond-evidence-{}-{suffix}-{}",
            std::process::id(),
            FIXTURE_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(root.join("nested")).unwrap();
        fs::write(root.join("z.txt"), b"z").unwrap();
        fs::write(root.join("nested/a.txt"), b"alpha").unwrap();
        root
    }

    #[test]
    fn manifest_is_sorted_and_verifies() {
        let root = fixture();
        let output = root.join("manifest.json");
        let manifest = create_manifest(&root, &output).unwrap();
        assert_eq!(
            manifest
                .files
                .iter()
                .map(|file| file.path.as_str())
                .collect::<Vec<_>>(),
            vec!["nested/a.txt", "z.txt"]
        );
        assert_eq!(verify_manifest(&root, &output).unwrap(), manifest);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn verification_detects_changed_and_extra_files() {
        let root = fixture();
        let output = root.join("manifest.json");
        create_manifest(&root, &output).unwrap();
        fs::write(root.join("z.txt"), b"changed").unwrap();
        fs::write(root.join("extra.txt"), b"extra").unwrap();
        let error = verify_manifest(&root, &output).unwrap_err().to_string();
        assert!(error.contains("z.txt"));
        assert!(error.contains("extra.txt"));
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn manifest_rejects_symlinks() {
        use std::os::unix::fs::symlink;
        let root = fixture();
        symlink(root.join("z.txt"), root.join("link.txt")).unwrap();
        let error = create_manifest(&root, &root.join("manifest.json"))
            .unwrap_err()
            .to_string();
        assert!(error.contains("symbolic links"));
        fs::remove_dir_all(root).unwrap();
    }
}
