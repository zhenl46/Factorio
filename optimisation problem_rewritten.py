import math
import os
import re
import tempfile
import time
from dataclasses import dataclass

import matplotlib.patches as patches
import matplotlib.pyplot as plt
import pulp

GRID_SIZE = 100
MIN_SPACING = 3
FORBIDDEN_SIZE = 4
FORBIDDEN_MIN = (GRID_SIZE - FORBIDDEN_SIZE) / 2
FORBIDDEN_MAX = FORBIDDEN_MIN + FORBIDDEN_SIZE
SOLVER_TIME_LIMIT = int(os.environ.get('SOLVER_TIME_LIMIT', '1200'))
CONGESTION_WEIGHT = 0.01
TARGET_ROUTING_SCORE = 2360
USE_HARDCODED_LAYOUT = False

blocks = {
    'Intake': (1, 1),
    'Oil_Processing': (18, 40),
    'Lava_Processing': (17, 43),
    'Circuits': (17, 54),
    'Red': (13, 16),
    'Green': (13, 14),
    'Blue': (25, 22),
    'Black': (13, 17),
    'Yellow': (13, 46),
    'Purple': (13, 23),
    'Orange': (16, 23),
    'Rocket': (12, 12),
    'Nuke_Crater': (27, 20),
}


@dataclass(frozen=True)
class Pin:
    block: str
    x: int
    y: int


@dataclass(frozen=True)
class Connection:
    source: str
    destination: str
    resource: str
    weight: float = 1.0


custom_pins = {
    'Oil_Processing_calcite': Pin('Oil_Processing', 1, 1),
    'Oil_Processing_fuel': Pin('Oil_Processing', 1, 4),
    'Oil_Processing_acid': Pin('Oil_Processing', 1, 3),
    'Oil_Processing_sulphur': Pin('Oil_Processing', 1, 9),
    'Oil_Processing_coal': Pin('Oil_Processing', 1, 39),
    'Oil_Processing_plastic': Pin('Oil_Processing', 4, 40),
    'Lava_Processing_lava': Pin('Lava_Processing', 1, 5),
    'Lava_Processing_calcite': Pin('Lava_Processing', 1, 2),
    'Lava_Processing_liq_cu': Pin('Lava_Processing', 17, 8),
    'Lava_Processing_liq_fe': Pin('Lava_Processing', 17, 24),
    'Lava_Processing_stone': Pin('Lava_Processing', 1, 43),
    'Lava_Processing_brick': Pin('Lava_Processing', 9, 43),
    'Lava_Processing_steel': Pin('Lava_Processing', 17, 20),
    'Circuits_liq_cu_in': Pin('Circuits', 1, 2),
    'Circuits_fe_out': Pin('Circuits', 1, 8),
    'Circuits_liq_fe_in': Pin('Circuits', 1, 11),
    'Circuits_green_out': Pin('Circuits', 1, 21),
    'Circuits_plastic_in': Pin('Circuits', 1, 46),
    'Circuits_acid_in': Pin('Circuits', 1, 51),
    'Circuits_red_blue_out': Pin('Circuits', 1, 54),
    'Red_liq_fe_in': Pin('Red', 1, 1),
    'Red_liq_cu_in': Pin('Red', 8, 1),
    'Red_sci_out': Pin('Red', 7, 16),
    'Red_gear_cu_out': Pin('Red', 13, 16),
    'Green_fe_gear': Pin('Green', 9, 1),
    'Green_circ': Pin('Green', 10, 1),
    'Green_out': Pin('Green', 7, 14),
    'Blue_engine_out': Pin('Blue', 1, 1),
    'Blue_liq_fe_in': Pin('Blue', 12, 1),
    'Blue_sulphur_in': Pin('Blue', 14, 1),
    'Blue_gear_in': Pin('Blue', 17, 1),
    'Blue_steel_in': Pin('Blue', 1, 20),
    'Blue_red_circ_in': Pin('Blue', 1, 22),
    'Blue_sci_out': Pin('Blue', 13, 22),
    'Black_cu_steel_in': Pin('Black', 1, 1),
    'Black_coal_fe_in': Pin('Black', 7, 1),
    'Black_brick_in': Pin('Black', 13, 1),
    'Black_sci_out': Pin('Black', 7, 17),
    'Yellow_engine_green_in': Pin('Yellow', 1, 1),
    'Yellow_steel_in': Pin('Yellow', 1, 9),
    'Yellow_heavy_oil_in': Pin('Yellow', 1, 17),
    'Yellow_liq_cu_in': Pin('Yellow', 1, 19),
    'Yellow_liq_fe_in': Pin('Yellow', 1, 22),
    'Yellow_plastic_in': Pin('Yellow', 1, 23),
    'Yellow_lds_out': Pin('Yellow', 1, 29),
    'Yellow_acid_in': Pin('Yellow', 12, 1),
    'Yellow_blue_circ_in': Pin('Yellow', 12, 1),
    'Yellow_fe_cu_in': Pin('Yellow', 13, 1),
    'Yellow_sci_out': Pin('Yellow', 7, 46),
    'Purple_circ_in': Pin('Purple', 5, 1),
    'Purple_red_circ_in': Pin('Purple', 5, 5),
    'Purple_steel_stone_in': Pin('Purple', 7, 1),
    'Purple_brick_in': Pin('Purple', 8, 1),
    'Purple_liq_fe_in': Pin('Purple', 12, 1),
    'Purple_sci_out': Pin('Purple', 7, 23),
    'Orange_acid_in': Pin('Orange', 1, 1),
    'Orange_coal_in': Pin('Orange', 1, 4),
    'Orange_liq_fe_in': Pin('Orange', 1, 8),
    'Orange_liq_cu_in': Pin('Orange', 1, 10),
    'Orange_tungsten_in': Pin('Orange', 14, 1),
    'Orange_sci_out': Pin('Orange', 1, 23),
}

connections = [
    Connection('Intake_C', 'Oil_Processing_calcite', 'Calcite'),
    Connection('Intake_C', 'Oil_Processing_acid', 'Acid'),
    Connection('Intake_C', 'Oil_Processing_coal', 'Coal'),
    Connection('Intake_C', 'Lava_Processing_calcite', 'Calcite'),
    Connection('Intake_C', 'Circuits_acid_in', 'Acid'),
    Connection('Intake_C', 'Black_coal_fe_in', 'Coal'),
    Connection('Intake_C', 'Orange_coal_in', 'Coal'),
    Connection('Intake_C', 'Orange_acid_in', 'Acid'),
    Connection('Intake_C', 'Orange_tungsten_in', 'Tungsten'),
    Connection('Intake_C', 'Yellow_acid_in', 'Acid'),
    Connection('Oil_Processing_fuel', 'Rocket_C', 'Rocket Fuel'),
    Connection('Oil_Processing_plastic', 'Circuits_plastic_in', 'Plastic'),
    Connection('Oil_Processing_plastic', 'Yellow_plastic_in', 'Plastic'),
    Connection('Oil_Processing_C', 'Yellow_heavy_oil_in', 'Heavy Oil'),
    Connection('Oil_Processing_sulphur', 'Blue_sulphur_in', 'Sulphur'),
    Connection('Nuke_Crater_C', 'Lava_Processing_lava', 'Lava'),
    Connection('Lava_Processing_stone', 'Nuke_Crater_C', 'Stone'),
    Connection('Lava_Processing_liq_fe', 'Circuits_liq_fe_in', 'Liquid Iron'),
    Connection('Lava_Processing_liq_cu', 'Circuits_liq_cu_in', 'Liquid Copper'),
    Connection('Lava_Processing_liq_fe', 'Red_liq_fe_in', 'Liquid Iron'),
    Connection('Lava_Processing_liq_cu', 'Red_liq_cu_in', 'Liquid Copper'),
    Connection('Lava_Processing_steel', 'Blue_steel_in', 'Steel'),
    Connection('Lava_Processing_liq_fe', 'Blue_liq_fe_in', 'Liquid Iron'),
    Connection('Lava_Processing_steel', 'Black_cu_steel_in', 'Steel'),
    Connection('Lava_Processing_brick', 'Black_brick_in', 'Stone Brick'),
    Connection('Lava_Processing_liq_fe', 'Yellow_liq_fe_in', 'Liquid Iron'),
    Connection('Lava_Processing_liq_cu', 'Yellow_liq_cu_in', 'Liquid Copper'),
    Connection('Lava_Processing_steel', 'Yellow_steel_in', 'Steel'),
    Connection('Lava_Processing_stone', 'Purple_steel_stone_in', 'Stone'),
    Connection('Lava_Processing_brick', 'Purple_brick_in', 'Stone Brick'),
    Connection('Lava_Processing_steel', 'Purple_steel_stone_in', 'Steel'),
    Connection('Lava_Processing_liq_fe', 'Purple_liq_fe_in', 'Liquid Iron'),
    Connection('Lava_Processing_liq_fe', 'Orange_liq_fe_in', 'Liquid Iron'),
    Connection('Lava_Processing_liq_cu', 'Orange_liq_cu_in', 'Liquid Copper'),
    Connection('Circuits_green_out', 'Green_circ', 'Green Circuits'),
    Connection('Circuits_green_out', 'Yellow_engine_green_in', 'Green Circuits'),
    Connection('Circuits_green_out', 'Purple_circ_in', 'Green Circuits'),
    Connection('Circuits_red_blue_out', 'Blue_red_circ_in', 'Red/Blue Circuits'),
    Connection('Circuits_red_blue_out', 'Yellow_engine_green_in', 'Red/Blue Circuits'),
    Connection('Circuits_red_blue_out', 'Yellow_blue_circ_in', 'Blue Circuits'),
    Connection('Circuits_red_blue_out', 'Purple_red_circ_in', 'Red Circuits'),
    Connection('Circuits_red_blue_out', 'Rocket_C', 'Blue Circuits'),
    Connection('Circuits_fe_out', 'Green_fe_gear', 'Iron Plates'),
    Connection('Circuits_fe_out', 'Black_coal_fe_in', 'Iron Plates'),
    Connection('Circuits_fe_out', 'Yellow_fe_cu_in', 'Iron Plates'),
    Connection('Red_sci_out', 'Rocket_C', 'Red Science'),
    Connection('Red_gear_cu_out', 'Green_fe_gear', 'Gears'),
    Connection('Red_gear_cu_out', 'Blue_gear_in', 'Gears'),
    Connection('Red_gear_cu_out', 'Black_cu_steel_in', 'Copper Plates'),
    Connection('Red_gear_cu_out', 'Yellow_fe_cu_in', 'Copper Plates'),
    Connection('Blue_engine_out', 'Yellow_engine_green_in', 'Engines'),
    Connection('Blue_sci_out', 'Rocket_C', 'Blue Science'),
    Connection('Yellow_sci_out', 'Rocket_C', 'Yellow Science'),
    Connection('Yellow_lds_out', 'Rocket_C', 'LDS'),
    Connection('Green_out', 'Rocket_C', 'Green Science'),
    Connection('Black_sci_out', 'Rocket_C', 'Black Science'),
    Connection('Purple_sci_out', 'Rocket_C', 'Purple Science'),
    Connection('Orange_sci_out', 'Rocket_C', 'Orange Science'),
]

pin_options = {
    'Lava_Processing_steel': ((17, 20), (1, 20)),
    'Lava_Processing_liq_fe': ((17, 24), (1, 9)),
    'Lava_Processing_stone': ((17, 43), (1, 43)),
    'Circuits_liq_cu_in': ((1, 2), (17, 2)),
    'Circuits_fe_out': ((1, 8), (17, 8)),
    'Circuits_liq_fe_in': ((1, 11), (17, 11)),
    'Circuits_green_out': ((1, 21), (17, 21)),
    'Circuits_plastic_in': ((1, 46), (17, 46)),
    'Circuits_acid_in': ((1, 51), (17, 51)),
    'Circuits_red_blue_out': ((1, 54), (17, 54)),
    'Red_gear_cu_out': ((13, 16), (1, 16)),
    'Blue_steel_in': ((1, 20), (25, 20)),
    'Blue_red_circ_in': ((1, 22), (25, 22)),
}


def validate_data():
    errors = []
    known_blocks = set(blocks)
    known_pins = set(custom_pins)
    if len(blocks) != len(set(blocks)):
        errors.append('Duplicate block name.')
    for name, pin in custom_pins.items():
        if pin.block not in known_blocks:
            errors.append(f'{name}: unknown owning block {pin.block}.')
        width, height = blocks[pin.block]
        if not 0 <= pin.x <= width or not 0 <= pin.y <= height:
            errors.append(f'{name}: local coordinate is outside its block.')
    for connection in connections:
        for endpoint in (connection.source, connection.destination):
            if endpoint.endswith('_C'):
                if endpoint[:-2] not in known_blocks:
                    errors.append(f'{endpoint}: unknown block center.')
            elif endpoint not in known_pins:
                errors.append(f'{endpoint}: unknown pin.')
        if connection.source in known_pins and connection.destination in known_pins:
            if custom_pins[connection.source].block == custom_pins[connection.destination].block:
                errors.append(f'{connection.source}->{connection.destination}: same-block route.')
    for pin_id, options in pin_options.items():
        if pin_id not in known_pins:
            errors.append(f'{pin_id}: option set has no matching pin.')
        if len(options) < 2:
            errors.append(f'{pin_id}: needs at least two alternatives.')
    if errors:
        raise ValueError('Input data validation failed:\n- ' + '\n- '.join(errors))


def calculate_big_m():
    largest_dimension = max(max(width, height) for width, height in blocks.values())
    return GRID_SIZE + largest_dimension + MIN_SPACING


def add_hardcoded_layout_constraints(problem, x, y, width, height):
    problem += x['Intake'] == 0
    problem += x['Nuke_Crater'] == MIN_SPACING
    problem += y['Nuke_Crater'] + height['Nuke_Crater'] == GRID_SIZE - MIN_SPACING

    for lower, upper in (
        ('Lava_Processing', 'Nuke_Crater'),
        ('Purple', 'Lava_Processing'),
        ('Orange', 'Purple'),
    ):
        problem += y[lower] + height[lower] <= y[upper]

    problem += x['Rocket'] + width['Rocket'] == FORBIDDEN_MIN
    problem += x['Rocket'] >= x['Purple'] + width['Purple'] + MIN_SPACING

    problem += x['Red'] == FORBIDDEN_MAX

    problem += x['Yellow'] >= x['Orange'] + width['Orange']
    problem += y['Yellow'] + height['Yellow'] <= y['Red']
    problem += y['Oil_Processing'] + height['Oil_Processing'] <= y['Yellow']

    problem += y['Red'] + height['Red'] <= y['Green']
    problem += y['Green'] + height['Green'] <= y['Black']
    problem += x['Blue'] >= x['Black'] + width['Black']
    problem += x['Blue'] >= x['Green'] + width['Green']

    problem += y['Circuits'] + height['Circuits'] <= y['Blue']
    for block in ('Green', 'Red', 'Yellow', 'Oil_Processing'):
        problem += x['Circuits'] >= x[block] + width[block]


def add_pin_coordinates(problem, x, y, rotation, orientation_cases):
    px = {}
    py = {}
    pin_cases = {}
    for pin_id, pin in custom_pins.items():
        original_width, original_height = blocks[pin.block]
        cases = orientation_cases[pin.block]
        options = pin_options.get(pin_id)
        if options is None:
            local_x, local_y = pin.x, pin.y
            px[pin_id] = x[pin.block] + pulp.lpSum(
                case * _transform_pin(local_x, local_y, original_width, original_height,
                                      rotated, flip_horizontal, flip_vertical)[0]
                for case, rotated, flip_horizontal, flip_vertical in cases
            )
            py[pin_id] = y[pin.block] + pulp.lpSum(
                case * _transform_pin(local_x, local_y, original_width, original_height,
                                      rotated, flip_horizontal, flip_vertical)[1]
                for case, rotated, flip_horizontal, flip_vertical in cases
            )
            continue

        pin_cases_for_pin = []
        for option_index, (local_x, local_y) in enumerate(options):
            for orientation_case, rotated, flip_horizontal, flip_vertical in cases:
                case = pulp.LpVariable(
                    f'pin_case_{pin_id}_{option_index}_{rotated}_{flip_horizontal}_{flip_vertical}',
                    cat='Binary',
                )
                problem += case <= orientation_case
                transformed_x, transformed_y = _transform_pin(
                    local_x, local_y, original_width, original_height,
                    rotated, flip_horizontal, flip_vertical,
                )
                pin_cases_for_pin.append((case, transformed_x, transformed_y))
        problem += pulp.lpSum(case for case, _, _ in pin_cases_for_pin) == 1
        pin_cases[pin_id] = pin_cases_for_pin
        px[pin_id] = x[pin.block] + pulp.lpSum(
            case * transformed_x for case, transformed_x, _ in pin_cases_for_pin
        )
        py[pin_id] = y[pin.block] + pulp.lpSum(
            case * transformed_y for case, _, transformed_y in pin_cases_for_pin
        )
    return px, py, pin_cases


def _transform_pin(local_x, local_y, original_width, original_height,
                   rotated, flip_horizontal, flip_vertical):
    if flip_horizontal:
        local_x = original_width - local_x
    if flip_vertical:
        local_y = original_height - local_y
    if rotated:
        return local_y, original_width - local_x
    return local_x, local_y


label_tokens = {
    'acid': 'Acid', 'brick': 'Brick', 'calcite': 'Calcite', 'circ': 'Circ',
    'coal': 'Coal', 'cu': 'Cu', 'engine': 'Engine', 'fe': 'Fe', 'fuel': 'Fuel',
    'gear': 'Gear', 'green': 'Green', 'heavy': 'Heavy', 'in': 'In', 'lds': 'LDS',
    'liq': 'Liq', 'oil': 'Oil', 'out': 'Out', 'plastic': 'Plastic', 'red': 'Red',
    'sci': 'Science', 'steel': 'Steel', 'stone': 'Stone', 'sulphur': 'Sulphur',
    'tungsten': 'Tungsten',
}

pin_label_overrides = {
    'Oil_Processing_fuel': 'Rocket Fuel', 'Lava_Processing_lava': 'Lava',
    'Lava_Processing_liq_cu': 'Liq Cu', 'Lava_Processing_liq_fe': 'Liq Fe',
    'Lava_Processing_steel': 'Steel', 'Lava_Processing_stone': 'Stone',
    'Lava_Processing_brick': 'Stone Brick', 'Circuits_liq_cu_in': 'Liq Cu',
    'Circuits_fe_out': 'Iron Plates', 'Circuits_acid_in': 'Acid',
    'Circuits_green_out': 'Green Circuits', 'Circuits_red_blue_out': 'Red/Blue Circuits',
    'Red_sci_out': 'Red Science', 'Red_gear_cu_out': 'Gears / Copper Plates',
    'Green_fe_gear': 'Iron Plates / Gears', 'Green_circ': 'Green Circuits',
    'Green_out': 'Green Science', 'Blue_engine_out': 'Engines',
    'Blue_sci_out': 'Blue Science', 'Black_cu_steel_in': 'Copper Plates / Steel',
    'Black_coal_fe_in': 'Coal / Iron Plates', 'Black_brick_in': 'Stone Bricks',
    'Black_sci_out': 'Black Science', 'Yellow_engine_green_in': 'Engines / Green Circuits',
    'Yellow_blue_circ_in': 'Blue Circuits', 'Yellow_fe_cu_in': 'Iron Plates / Copper Plates',
    'Yellow_sci_out': 'Yellow Science', 'Purple_circ_in': 'Green Circuits',
    'Purple_red_circ_in': 'Red Circuits', 'Purple_steel_stone_in': 'Steel / Stone',
    'Purple_brick_in': 'Stone Brick', 'Purple_sci_out': 'Purple Science',
    'Orange_sci_out': 'Orange Science',
}


def pin_label(pin_id):
    if pin_id in pin_label_overrides:
        return pin_label_overrides[pin_id]
    block_name = next((block for block in blocks if pin_id.startswith(f'{block}_')), '')
    pin_name = pin_id[len(block_name) + 1:] if block_name else pin_id
    words = [label_tokens.get(word, word.title()) for word in pin_name.split('_')]
    if len(words) > 1 and words[-1] in {'In', 'Out'}:
        words.pop()
    elif words == ['In']:
        words = ['Input']
    elif words == ['Out']:
        words = ['Output']
    return ' '.join(words)


def labels_overlap(first, second):
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return (abs(first_x - second_x) * 2 < first_width + second_width and
            abs(first_y - second_y) * 2 < first_height + second_height)


def build_model():
    validate_data()
    big_m = calculate_big_m()
    problem = pulp.LpProblem('Macro_Layout', pulp.LpMinimize)
    x = {block: pulp.LpVariable(f'x_{block}', 0, GRID_SIZE) for block in blocks}
    y = {block: pulp.LpVariable(f'y_{block}', 0, GRID_SIZE) for block in blocks}
    rotation = {block: pulp.LpVariable(f'rotation_{block}', cat='Binary') for block in blocks}
    flip_horizontal = {block: pulp.LpVariable(f'flip_horizontal_{block}', cat='Binary') for block in blocks}
    flip_vertical = {block: pulp.LpVariable(f'flip_vertical_{block}', cat='Binary') for block in blocks}
    width = {block: pulp.LpVariable(f'width_{block}', lowBound=0) for block in blocks}
    height = {block: pulp.LpVariable(f'height_{block}', lowBound=0) for block in blocks}
    orientation_cases = {}

    for block in blocks:
        if block in {'Intake', 'Rocket', 'Nuke_Crater'}:
            problem += rotation[block] == 0
            problem += flip_horizontal[block] == 0
            problem += flip_vertical[block] == 0
            orientation_cases[block] = []
            continue
        cases = []
        for rotated in (0, 1):
            for horizontal in (0, 1):
                for vertical in (0, 1):
                    case = pulp.LpVariable(
                        f'orientation_{block}_{rotated}_{horizontal}_{vertical}', cat='Binary'
                    )
                    cases.append((case, rotated, horizontal, vertical))
        problem += pulp.lpSum(case for case, _, _, _ in cases) == 1
        problem += rotation[block] == pulp.lpSum(case * rotated for case, rotated, _, _ in cases)
        problem += flip_horizontal[block] == pulp.lpSum(case * horizontal for case, _, horizontal, _ in cases)
        problem += flip_vertical[block] == pulp.lpSum(case * vertical for case, _, _, vertical in cases)
        orientation_cases[block] = cases

    for block, (original_width, original_height) in blocks.items():
        problem += width[block] == original_width * (1 - rotation[block]) + original_height * rotation[block]
        problem += height[block] == original_height * (1 - rotation[block]) + original_width * rotation[block]
        if block == 'Intake':
            problem += x[block] + width[block] <= GRID_SIZE
            problem += y[block] + height[block] <= GRID_SIZE
        else:
            problem += x[block] >= MIN_SPACING
            problem += y[block] >= MIN_SPACING
            problem += x[block] + width[block] <= GRID_SIZE - MIN_SPACING
            problem += y[block] + height[block] <= GRID_SIZE - MIN_SPACING

    if USE_HARDCODED_LAYOUT:
        add_hardcoded_layout_constraints(problem, x, y, width, height)

    px, py, pin_cases = add_pin_coordinates(problem, x, y, rotation, orientation_cases)

    edge_left = pulp.LpVariable('edge_left', cat='Binary')
    edge_right = pulp.LpVariable('edge_right', cat='Binary')
    edge_bottom = pulp.LpVariable('edge_bottom', cat='Binary')
    edge_top = pulp.LpVariable('edge_top', cat='Binary')
    problem += edge_left + edge_right + edge_bottom + edge_top == 1
    problem += x['Intake'] <= GRID_SIZE * (1 - edge_left)
    problem += x['Intake'] >= GRID_SIZE * edge_right
    problem += y['Intake'] <= GRID_SIZE * (1 - edge_bottom)
    problem += y['Intake'] >= GRID_SIZE * edge_top

    for index, first in enumerate(blocks):
        for second in list(blocks)[index + 1:]:
            spacing = 0 if 'Intake' in (first, second) else MIN_SPACING
            left = pulp.LpVariable(f'left_{first}_{second}', cat='Binary')
            right = pulp.LpVariable(f'right_{first}_{second}', cat='Binary')
            below = pulp.LpVariable(f'below_{first}_{second}', cat='Binary')
            above = pulp.LpVariable(f'above_{first}_{second}', cat='Binary')
            problem += left + right + below + above == 1
            problem += x[first] + width[first] + spacing <= x[second] + big_m * (1 - left)
            problem += x[second] + width[second] + spacing <= x[first] + big_m * (1 - right)
            problem += y[first] + height[first] + spacing <= y[second] + big_m * (1 - below)
            problem += y[second] + height[second] + spacing <= y[first] + big_m * (1 - above)

    for block in blocks:
        left = pulp.LpVariable(f'forbidden_left_{block}', cat='Binary')
        right = pulp.LpVariable(f'forbidden_right_{block}', cat='Binary')
        below = pulp.LpVariable(f'forbidden_below_{block}', cat='Binary')
        above = pulp.LpVariable(f'forbidden_above_{block}', cat='Binary')
        problem += left + right + below + above == 1
        problem += x[block] + width[block] <= FORBIDDEN_MIN + big_m * (1 - left)
        problem += x[block] >= FORBIDDEN_MAX - big_m * (1 - right)
        problem += y[block] + height[block] <= FORBIDDEN_MIN + big_m * (1 - below)
        problem += y[block] >= FORBIDDEN_MAX - big_m * (1 - above)

    route_lengths = []
    for index, connection in enumerate(connections):
        source_x = x[connection.source[:-2]] if connection.source.endswith('_C') else px[connection.source]
        source_y = y[connection.source[:-2]] if connection.source.endswith('_C') else py[connection.source]
        if connection.source.endswith('_C'):
            source_x += width[connection.source[:-2]] / 2
            source_y += height[connection.source[:-2]] / 2
        destination_x = x[connection.destination[:-2]] if connection.destination.endswith('_C') else px[connection.destination]
        destination_y = y[connection.destination[:-2]] if connection.destination.endswith('_C') else py[connection.destination]
        if connection.destination.endswith('_C'):
            destination_x += width[connection.destination[:-2]] / 2
            destination_y += height[connection.destination[:-2]] / 2
        dx = pulp.LpVariable(f'dx_{index}', lowBound=0)
        dy = pulp.LpVariable(f'dy_{index}', lowBound=0)
        problem += dx >= source_x - destination_x
        problem += dx >= destination_x - source_x
        problem += dy >= source_y - destination_y
        problem += dy >= destination_y - source_y
        route_length = pulp.LpVariable(f'route_length_{index}', lowBound=0)
        problem += route_length == dx + dy
        route_lengths.append((connection.weight, route_length))

    congestion_proxy = pulp.LpVariable('congestion_proxy', lowBound=0)
    for _, route_length in route_lengths:
        problem += congestion_proxy >= route_length
    problem += pulp.lpSum(weight * length for weight, length in route_lengths) + CONGESTION_WEIGHT * congestion_proxy
    return problem, (x, y, width, height, rotation, px, py, pin_cases)


def solve_and_report():
    attempt = 0
    while True:
        attempt += 1
        problem, variables = build_model()
        log_path = None
        solve_start = time.perf_counter()
        try:
            with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as log_file:
                log_path = log_file.name
            status_code = problem.solve(
                pulp.PULP_CBC_CMD(
                    timeLimit=SOLVER_TIME_LIMIT,
                    msg=False,
                    logPath=log_path,
                    options=['randomSeed', str(attempt)],
                )
            )
        finally:
            solve_duration = time.perf_counter() - solve_start
        configurations = None
        if log_path is not None:
            try:
                with open(log_path, encoding='utf-8') as log_file:
                    log_text = log_file.read()
                match = re.search(r'Enumerated nodes:\s*(\d+)', log_text)
                if match:
                    configurations = int(match.group(1))
            finally:
                os.unlink(log_path)
        status = pulp.LpStatus.get(status_code, 'Unknown')
        objective = pulp.value(problem.objective)
        if status in {'Optimal', 'Feasible'} and objective is not None and objective < TARGET_ROUTING_SCORE:
            x, y, width, height = variables[:4]
            print(f'Solver status: {status}')
            print(f'Solver time: {solve_duration:.2f} seconds')
            print(f'Attempt: {attempt}')
            if configurations is not None:
                print(f'Configurations analysed: {configurations}')
            print(f'Objective including congestion proxy: {objective:.2f}')
            crater_center_x = x['Nuke_Crater'].value() + width['Nuke_Crater'].value() / 2
            crater_center_y = y['Nuke_Crater'].value() + height['Nuke_Crater'].value() / 2
            print(f'Nuke crater center: ({crater_center_x:.2f}, {crater_center_y:.2f})')
            return problem, variables
        print(f'Attempt {attempt}: status={status}, score={objective}. Retrying until score < {TARGET_ROUTING_SCORE}.')


def show_layout(result):
    problem, (x, y, width, height, rotation, px, py, _) = result
    fig, ax = plt.subplots(figsize=(14, 14))
    ax.set_xlim(0, GRID_SIZE)
    ax.set_ylim(0, GRID_SIZE)
    ax.set_aspect('equal')
    ax.add_patch(patches.Rectangle((MIN_SPACING, MIN_SPACING), GRID_SIZE - 2 * MIN_SPACING,
                                   GRID_SIZE - 2 * MIN_SPACING, fill=False, edgecolor='red', linestyle='--'))
    ax.add_patch(patches.Rectangle((FORBIDDEN_MIN, FORBIDDEN_MIN), FORBIDDEN_SIZE, FORBIDDEN_SIZE,
                                   facecolor='orange', edgecolor='darkorange', hatch='xx', alpha=0.35))
    science_colors = {
        'Red': '#ffb3b3', 'Green': '#b8e6b8', 'Blue': '#b3d9ff',
        'Black': '#b8b8b8', 'Yellow': '#fff0a8', 'Purple': '#d9b3ff', 'Orange': '#ffd0a8',
    }
    for block in blocks:
        final_x, final_y = x[block].value(), y[block].value()
        final_width, final_height = width[block].value(), height[block].value()
        if block == 'Intake':
            ax.plot(final_x, final_y, 'ro', markersize=10)
            ax.text(final_x, final_y - 2, 'Intake', ha='center', color='red', weight='bold')
            continue
        if block == 'Nuke_Crater':
            shape = patches.Ellipse((final_x + final_width / 2, final_y + final_height / 2),
                                    final_width, final_height, facecolor='lightgray',
                                    edgecolor='red', hatch='//')
        else:
            shape = patches.Rectangle((final_x, final_y), final_width, final_height,
                                      facecolor=science_colors.get(block, '#e6f2ff'), edgecolor='black')
        ax.add_patch(shape)
        ax.text(final_x + final_width / 2, final_y + final_height / 2,
            block.replace('_', ' '), ha='center', va='center', weight='bold', fontsize=16)

    label_boxes = []
    for pin_id, pin in custom_pins.items():
        point_x, point_y = pulp.value(px[pin_id]), pulp.value(py[pin_id])
        marker = 'x' if any(connection.source == pin_id for connection in connections) else 'o'
        ax.plot(point_x, point_y, marker, color='black', markersize=5, markeredgewidth=1.5)
        block_center_x = pulp.value(x[pin.block] + width[pin.block] / 2)
        block_center_y = pulp.value(y[pin.block] + height[pin.block] / 2)
        label = pin_label(pin_id).replace(' Science', ' Sci').replace(' / ', ' /\n')
        label_lines = label.splitlines()
        label_width = max(2.5, max(len(line) for line in label_lines) * 0.48)
        label_height = len(label_lines) * 1.2
        final_x, final_y = x[pin.block].value(), y[pin.block].value()
        final_width, final_height = width[pin.block].value(), height[pin.block].value()
        candidates = [(point_x, point_y)]
        for offset_x, offset_y in (
            (1.5, 0), (-1.5, 0), (0, 1.5), (0, -1.5),
            (1.5, 1.5), (-1.5, 1.5), (-1.5, -1.5), (1.5, -1.5),
            (3, 0), (-3, 0), (0, 3), (0, -3),
        ):
            candidates.append((point_x + offset_x, point_y + offset_y))
        candidates.append((block_center_x, block_center_y))
        for row in range(1, 6):
            for column in range(1, 6):
                candidates.append((final_x + final_width * column / 6,
                                   final_y + final_height * row / 6))
        candidates.sort(key=lambda candidate: math.dist(candidate, (point_x, point_y)))

        chosen_position = None
        for label_x, label_y in candidates:
            candidate_box = (label_x, label_y, label_width, label_height)
            inside_block = (
                final_x + 0.8 + label_width / 2 <= label_x <= final_x + final_width - 0.8 - label_width / 2 and
                final_y + 0.8 + label_height / 2 <= label_y <= final_y + final_height - 0.8 - label_height / 2
            )
            if inside_block and not any(labels_overlap(candidate_box, box) for box in label_boxes):
                chosen_position = (label_x, label_y)
                label_boxes.append(candidate_box)
                break

        if chosen_position is None:
            chosen_position = (block_center_x, block_center_y)
        ax.text(chosen_position[0], chosen_position[1], label,
                ha='center', va='center', fontsize=6, color='darkred', weight='bold')

    for connection in connections:
        source = connection.source
        destination = connection.destination
        source_x = pulp.value(px[source]) if source in px else pulp.value(x[source[:-2]] + width[source[:-2]] / 2)
        source_y = pulp.value(py[source]) if source in py else pulp.value(y[source[:-2]] + height[source[:-2]] / 2)
        destination_x = pulp.value(px[destination]) if destination in px else pulp.value(x[destination[:-2]] + width[destination[:-2]] / 2)
        destination_y = pulp.value(py[destination]) if destination in py else pulp.value(y[destination[:-2]] + height[destination[:-2]] / 2)
        ax.plot([source_x, destination_x], [source_y, destination_y], color='gray', alpha=0.35, linewidth=0.6)
    ax.set_title(f'Macro-Cell Layout with Advanced Pin Mapping\nRouting Score: {pulp.value(problem.objective):.1f}', fontsize=18)
    ax.grid(True, linestyle=':', alpha=0.5)
    image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'macro-cell-layout.png')
    fig.savefig(image_path, dpi=200, bbox_inches='tight')
    print(f'Layout image saved to: {image_path}')
    plt.show()


if __name__ == '__main__':
    result = solve_and_report()
    if result is not None:
        show_layout(result)
