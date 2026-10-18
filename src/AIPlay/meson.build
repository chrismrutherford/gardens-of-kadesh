# AI Play layer (feature 002: AI plays while the user spectates)
# Pure core + engine backend. See specs/002-ai-plays-the-game-via-a-spectating-ui/plan.md

inc_aiplay = include_directories('.')

lib_aiplay = static_library(
    'aiplay',
    [
        'aiplay.c',
        'aiplay_orders.c',
        'aiplay_snapshot.c',
        'aiplay_transport.c',
        'aiplay_json.c',
    ],
    include_directories: inc_aiplay,
)

# engine glue: compiled into the game executable (needs engine include paths)
aiplay_engine_glue = files('aiplay_engine.c')

# unit tests: pure core + stub engine backend (-DAIPLAY_UNIT_TEST)
inc_aiplay = include_directories('.')

aiplay_test_src = [
    'aiplay.c',
    'aiplay_orders.c',
    'aiplay_transport.c',
    'aiplay_json.c',
    'tests/stub_backend.c',
    'tests/test_json.c',
    'tests/test_session.c',
    'tests/test_transport.c',
    'tests/test_main.c',
]

aiplay_tests = executable(
    'aiplay-tests',
    aiplay_test_src,
    include_directories: inc_aiplay,
    c_args: ['-DAIPLAY_UNIT_TEST', '-Wno-unused-parameter'],
)

test('aiplay-tests', aiplay_tests)