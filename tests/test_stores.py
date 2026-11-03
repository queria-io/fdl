"""The S3 store against moto: conditions, missing objects and listing."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from fdl import refs, repo
from fdl.stores import HttpStore, LocalStore, S3Store, StoreError, open_store


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="lake")
        yield S3Store("lake", "datasets/towns/", client=client)


def test_conditions_are_kept(s3: S3Store) -> None:
    etag = s3.put("a", b"1", if_none_match="*")
    with pytest.raises(StoreError) as refused:
        s3.put("a", b"2", if_none_match="*")
    assert refused.value.status == 412
    s3.put("a", b"2", if_match=etag)
    assert s3.get("a") == b"2"


def test_missing_and_listing(s3: S3Store) -> None:
    assert s3.head("nothing") is None
    with pytest.raises(StoreError) as missing:
        s3.get("nothing")
    assert missing.value.status == 404
    s3.put("x/1", b"")
    s3.put("x/2", b"")
    assert sorted(s3.list("x/")) == ["x/1", "x/2"]
    s3.delete(["x/1"])
    assert s3.list("x/") == ["x/2"]


def test_a_repository_on_s3(s3: S3Store) -> None:
    repo.init(s3)
    assert refs.resolve(s3) == ("refs/heads/main", None)
    assert s3.url == "s3://lake/datasets/towns/"
    assert repo.data_url(s3) == "s3://lake/datasets/towns/data/"


def test_open_store(tmp_path) -> None:
    assert isinstance(open_store(str(tmp_path)), LocalStore)
    assert isinstance(open_store("https://example.org/lake/"), HttpStore)
    with pytest.raises(StoreError):
        open_store("ftp://example.org/lake/")


@pytest.fixture
def helper(tmp_path, monkeypatch):
    """fdl's config with a helper for data.example.org/towns/ only."""
    script = tmp_path / "helper.sh"
    script.write_text(
        "#!/bin/sh\n"
        '[ "$1" = get ] || exit 0\n'
        "input=$(cat)\n"
        'case "$input" in *host=data.example.org*) printf "username=x\\npassword=tok-1\\n";; esac\n'
    )
    script.chmod(0o755)
    config = tmp_path / "config.toml"
    config.write_text(f'[credential."https://data.example.org/towns"]\nhelper = "{script}"\n')
    monkeypatch.setenv("FDL_CONFIG", str(config))
    monkeypatch.delenv("FDL_TOKEN", raising=False)


def test_a_token_comes_from_the_helper_in_fdls_config(helper) -> None:
    assert HttpStore("https://data.example.org/towns/").token == "tok-1"
    # Another host or another path has no helper, and nobody is asked.
    assert HttpStore("https://other.example.org/towns/").token is None
    assert HttpStore("https://data.example.org/townsfolk/").token is None


def test_fdl_token_wins_over_the_helper(helper, monkeypatch) -> None:
    monkeypatch.setenv("FDL_TOKEN", "from-env")
    assert HttpStore("https://data.example.org/towns/").token == "from-env"


def test_no_config_means_no_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FDL_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.delenv("FDL_TOKEN", raising=False)
    assert HttpStore("https://data.example.org/towns/").token is None
