"""CLI entrypoint for IPT DB preflight operations."""

import datetime

import click

from ipdb.manager import IptDbManager


@click.group()
def cli() -> None:
    """IP DB lifecycle operations."""


@cli.command("prepare")
def prepare() -> None:
    manager = IptDbManager()
    try:
        result = manager.prepare(now_utc=datetime.datetime.now(datetime.UTC))
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"source={result.source} reason={result.reason}")


if __name__ == "__main__":
    cli()
