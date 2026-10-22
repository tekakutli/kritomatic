use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");

    let batch: leader_optimizer::types::BatchRequest =
        serde_json::from_str(&input).expect("parse request json");

    let response = leader_optimizer::optimize_batch(batch);

    let out = serde_json::to_string(&response).expect("serialize response");
    io::stdout().write_all(out.as_bytes()).expect("write stdout");
    io::stdout().write_all(b"\n").expect("write newline");
}
