"""Print a greeting for the warehouse example."""


def hello(name="world"):
    """Return a greeting for a name."""
    return "Hello, {}!".format(name)


if __name__ == "__main__":
    print(hello())
