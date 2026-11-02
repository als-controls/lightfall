from lightfall.content.labels import describe_source


def test_describe_source():
    assert describe_source("core") == "Built-in"
    assert describe_source("builtin") == "Built-in"
    assert describe_source("package") == "Package"
    assert describe_source("plugin") == "Plugin"
    assert describe_source("local") == "Mine"
    assert describe_source("facility") == "Facility"
    assert describe_source("beamline:7.0.1") == "Beamline 7.0.1"
    assert describe_source("branch:7.0.1.1") == "Branch 7.0.1.1"
    assert describe_source("") == "this scope"
