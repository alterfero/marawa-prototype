from app.compute.specificity import classify


def test_researcher_levels_and_direction():
    assert classify("A NON-HUMAN follows a human") == (1, "researcher")
    assert classify("a snake follows a human after being disturbed") == (4, "researcher")
    assert classify("a ritual requires the killing of pigs over several days") == (3, "disputed")
    assert classify("jealousy between sisters")[1] == "provisional"
    assert classify("a sea turtle carries a child home")[0] in (1, 2, 3, 4)
