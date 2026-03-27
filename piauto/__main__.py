"""PiAuto entry point — run with `python -m piauto`."""

from piauto.statemachine import StateMachine


def main() -> None:
    sm = StateMachine()
    sm.run()


if __name__ == "__main__":
    main()
