"""
protocols.py

Transforms aggregated protocols (Exchange-PAINT, MERPAINT, ...)
into linearized protocols for the various subsystems (fluidics, imaging,
illumination)

e.g.
protocol_fluid = [
    {'type': 'inject', 'reservoir_id': 0, 'volume': 500},
    {'type': 'incubate', 'duration': 120},
    {'type': 'inject', 'reservoir_id': 1, 'volume': 500, 'velocity': 600},
    {'type': 'signal', 'value': 'fluid round 1 done'},
    {'type': 'flush', 'flushfactor': 1},
    {'type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
    {'type': 'inject', 'reservoir_id': 20, 'volume': 500},
]

protocol_imaging = [
    {'type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'type': 'acquire', 'frames': 10000, 't_exp': 100, 'round': 1},
    {'type': 'signal', 'value': 'imaging round 1 done'},
]

protocol_illumination = [
    {'type': 'power', 'value': 1},
    {'type': 'wait for signal', 'target': 'fluid', 'value': 'round 1 done'},
    {'type': 'power', 'value': 50},
    {'type': 'wait for signal', 'target': 'imaging', 'value': 'round 1 done'},
]
"""
