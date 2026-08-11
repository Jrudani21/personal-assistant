# SPEC — rewrite assistant/deepseek_key.py

Output only the complete file in one fenced python block.

Same public API: `find_key() -> str` and `key_source() -> str`.
Same resolution order, first non-empty wins:

1. env `DEEPSEEK_API_KEY`                      label `env:DEEPSEEK_API_KEY`
2. file at env `KEN_DEEPSEEK_KEY_FILE`         label `file:<path>`
3. `<repo>/data/.deepseek_key`                 label `repo:data/.deepseek_key`
4. `~/.deepseek_key`                           label `home:~/.deepseek_key`
5. `C:/Users/Janak's PC/.deepseek_key`         label `absolute:<path>`

Repo root = parent of the `assistant` package. Values stripped. Stdlib only.

Fix two defects in the current version:

1. `Path.home()` is evaluated OUTSIDE the try, so if it raises the whole
   lookup dies. It CAN raise RuntimeError when no home directory is
   resolvable - precisely the "scheduled task started at boot with no user
   profile loaded" case this module exists to survive. Every candidate,
   including computing its path, must be individually guarded against
   Exception.

2. `find_key()` and `key_source()` each re-implement the whole chain. They
   must not be able to disagree. Factor the chain into ONE private helper
   returning ordered `(label, key)` candidates (lazily, so later files are
   not read once an earlier source wins), and implement both public
   functions on top of it. `key_source()` must report the source that
   `find_key()` would actually return.

Add a `__main__` block printing `key_source()` and whether a key was found -
never the key itself. Comment only the non-obvious (why each guard exists,
why the absolute fallback is hardcoded).
