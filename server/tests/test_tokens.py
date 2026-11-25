import tomllib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from syncbaton.tokens import add_user_hash, hash_token, new_token

DIGEST = "a" * 64
OTHER = "b" * 64

BASE = """\
# syncbaton config
listen = "0.0.0.0:8790"

[syncthing]
url = "http://127.0.0.1:8384"  # local
api_key_file = "/k"

[users]
# people
sam = "OTHER_HASH"

[[folder]]
id = "music"
path = "/srv/music"
""".replace("OTHER_HASH", OTHER)


def users(text):
    return tomllib.loads(text).get("users", {})


def test_hash_is_sha256_hex_of_utf8():
    assert hash_token("abc") == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_new_tokens_are_distinct_and_url_safe():
    tokens = {new_token() for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 43 and t.replace("-", "").replace("_", "").isalnum() for t in tokens)


def test_inserts_into_existing_table_and_keeps_everything_else():
    out = add_user_hash(BASE, "alex", DIGEST)
    assert users(out) == {"sam": OTHER, "alex": DIGEST}
    assert out.replace(f'alex = "{DIGEST}"\n', "") == BASE


def test_inserted_line_follows_last_key_not_following_table():
    out = add_user_hash(BASE, "alex", DIGEST).splitlines()
    assert out[out.index(f'sam = "{OTHER}"') + 1] == f'alex = "{DIGEST}"'


def test_replaces_existing_user_line_in_place():
    out = add_user_hash(BASE, "sam", DIGEST)
    assert users(out) == {"sam": DIGEST}
    assert out == BASE.replace(OTHER, DIGEST)


def test_replaces_quoted_key():
    text = f'[users]\n"sam" = "{OTHER}"\n'
    assert users(add_user_hash(text, "sam", DIGEST)) == {"sam": DIGEST}


def test_creates_table_when_missing():
    text = '[syncthing]\nurl = "http://x"'
    out = add_user_hash(text, "alex", DIGEST)
    assert users(out) == {"alex": DIGEST}
    assert out.startswith(text)


def test_creates_table_in_empty_file():
    assert users(add_user_hash("", "alex", DIGEST)) == {"alex": DIGEST}


def test_empty_users_table_at_end_without_newline():
    assert users(add_user_hash("[users]", "alex", DIGEST)) == {"alex": DIGEST}


def test_header_with_comment_and_spacing():
    text = f'[ users ]  # who\nsam = "{OTHER}"\n'
    assert users(add_user_hash(text, "alex", DIGEST)) == {"sam": OTHER, "alex": DIGEST}


@pytest.mark.parametrize("name", ["", "a b", "a.b", 'a"b', "a=b", "ü", "a\nb"])
def test_rejects_unsafe_user_names(name):
    with pytest.raises(ValueError, match="user name"):
        add_user_hash(BASE, name, DIGEST)


def test_refuses_when_result_would_not_contain_the_hash():
    with pytest.raises(ValueError, match="invalid TOML"):
        add_user_hash('users = { sam = "x" }\n', "alex", DIGEST)


names = st.from_regex(r"[A-Za-z0-9_-]{1,12}", fullmatch=True)


@given(existing=st.dictionaries(names, st.just(OTHER), max_size=4), new=names)
def test_property_all_users_kept_and_other_lines_untouched(existing, new):
    body = "".join(f'{k} = "{v}"\n' for k, v in existing.items())
    text = f'listen = "x:1"\n\n[users]\n{body}\n[syncthing]\nurl = "http://x"\n'
    out = add_user_hash(text, new, DIGEST)
    assert users(out) == {**existing, new: DIGEST}
    kept = [line for line in out.splitlines() if new not in line.split("=")[0].split()]
    expected = [line for line in text.splitlines() if new not in line.split("=")[0].split()]
    assert kept == expected
