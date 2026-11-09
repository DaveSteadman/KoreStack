from KoreCommon.suite_paths import get_koredata_dir, load_config


cfg = load_config("koretex", {
    "port": None,
    "host": "127.0.0.1",
    "log_level": "info",
    "data_dir": str(get_koredata_dir() / "Tex"),
})
