"""Static guard: Router.py must not contain any 'add' kernel write literal."""

import unittest


class TestNoAddCallSitesRemainInRouter(unittest.TestCase):
    """Static guard: Router.py must not contain any 'add' kernel write literal."""

    def test_router_source_has_no_add_route_literals(self):
        """Router.py source must not contain ipr/batch 'add' route literals.

        This guards against accidental regression of any call site back to
        'add'. The watcher design requires uniform 'replace' semantics.
        The 'del' literal is still permitted and is not checked here.
        """
        import pathlib

        router_path = pathlib.Path(__file__).parent.parent / "Router.py"
        source = router_path.read_text()

        forbidden = [
            'route("add"',
            "route('add'",
            '("add", ',
            "('add', ",
        ]
        found = [lit for lit in forbidden if lit in source]
        self.assertEqual(
            found,
            [],
            f"Router.py still contains forbidden 'add' literals: {found}",
        )


if __name__ == "__main__":
    unittest.main()
