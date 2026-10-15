"""Analysis layer: consumes generated raw CAN observations; never part of the generator.

raw CAN -> 100-frame windows -> FLEET-GUARD behavioural descriptors (+ observable timestamps).
Ground truth is joined only afterwards, into separate evaluation files.
"""
