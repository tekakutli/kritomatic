def main() -> int:
    ap = argparse.ArgumentParser(...)  # DELETE
    ...                                  # DELETE all args
    args = ap.parse_args()               # DELETE
    
    if args.no_llm: args.llm_mode = "off"  # DELETE (handled in CLI)
    
    if not args.dump.exists(): ...        # KEEP but use cfg.dump_path
    dump_text = args.dump.read_text()     # → cfg.dump_path.read_text()
    dump_sha = hashlib.sha256(dump_text.encode()).hexdigest()
    args.out.mkdir(parents=True, exist_ok=True)  # → cfg.out_dir
    
    remap: dict[str, str] = {}            # → cfg.remap already dict
    for item in args.remap: ...           # DELETE (CLI parses)
    
    llm = None                             # keep, but use cfg
    if args.llm_mode != "off":
        cache = args.cache_dir or (args.out / "cache" / "llm")
        llm = LLMClient(args.llm_url, args.llm_model, cache,
                        max_calls=args.llm_max_calls,
                        timeout=args.llm_timeout)
        if args.llm_ping: ...              # keep, use cfg
    
    ...                                    # rest same
    
    rc = pass9_write(records, blocks, deleted_files, args.out,
                     args.layout, remap, args.min_trust, args.dump,
                     dump_sha, dump_text, llm, args.llm_mode,
                     cross_validate, args.trace)
    ...
