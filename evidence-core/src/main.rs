use lotuspond_evidence::signing::{generate_keypair, sign_manifest, verify_signed_manifest};
use lotuspond_evidence::{create_manifest, verify_manifest};
use std::path::Path;

fn usage() -> ! {
    eprintln!(
        "usage:\n  lotuspond-evidence create <artifact-directory> <manifest.json>\n  lotuspond-evidence verify <artifact-directory> <manifest.json>\n  lotuspond-evidence keygen <private-key.pem> <public-key.pem>\n  lotuspond-evidence sign <manifest.json> <private-key.pem> <signature.json>\n  lotuspond-evidence verify-signed <artifact-directory> <manifest.json> <signature.json> <trusted-public-key.pem>"
    );
    std::process::exit(2);
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    let path = |index: usize| Path::new(&arguments[index]);
    let result = match (arguments.get(1).map(String::as_str), arguments.len()) {
        (Some("create"), 4) => create_manifest(path(2), path(3)).map(|manifest| {
            println!(
                "created {} entries in {}",
                manifest.files.len(),
                arguments[3]
            );
        }),
        (Some("verify"), 4) => verify_manifest(path(2), path(3)).map(|manifest| {
            println!("verified {} entries", manifest.files.len());
        }),
        (Some("keygen"), 4) => generate_keypair(path(2), path(3)).map(|key_id| {
            println!("created key {key_id}; keep {} private", arguments[2]);
        }),
        (Some("sign"), 5) => sign_manifest(path(2), path(3), path(4)).map(|signature| {
            println!("signed {} with key {}", arguments[2], signature.key_id);
        }),
        (Some("verify-signed"), 6) => verify_signed_manifest(path(2), path(3), path(4), path(5))
            .map(|(manifest, key_id)| {
                println!(
                    "verified {} entries signed by key {key_id}",
                    manifest.files.len()
                );
            }),
        _ => usage(),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
