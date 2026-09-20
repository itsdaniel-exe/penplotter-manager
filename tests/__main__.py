"""Run every test module:  .venv\\Scripts\\python -m tests"""

from __future__ import annotations

from . import (_harness, test_calibration, test_console, test_formatting, test_inputs,
               test_notebook)

if __name__ == "__main__":
    print("=== machine calibration ===\n")
    _harness.run([
        test_calibration.test_page_orientation,
        test_calibration.test_pen_mapping,
        test_calibration.test_servo_values_are_sent,
        test_calibration.test_go_to_zero,
        test_calibration.test_serial_port_ownership,
        test_calibration.test_silent_board_does_not_hang,
        test_calibration.test_cancel_stops_cleanly,
        test_calibration.test_pause_does_not_time_out,
        test_calibration.test_pen_state_is_not_guessed,
    ])
    print("=== text formatting ===\n")
    _harness.run([
        test_formatting.test_autofit,
        test_formatting.test_handwriting_is_reproducible,
        test_formatting.test_handwriting_varies_letters,
        test_formatting.test_handwriting_stays_on_the_paper,
    ])
    print("=== input handling ===\n")
    _harness.run([
        test_inputs.test_word_punctuation_is_drawable,
        test_inputs.test_unsupported_characters_are_reported,
        test_inputs.test_long_words_are_broken,
        test_inputs.test_bad_sizes_say_which_field_is_wrong,
        test_inputs.test_svg_paths,
        test_inputs.test_svg_document,
    ])
    print("=== notebook (ruled pages, several pens) ===\n")
    _harness.run([
        test_notebook.test_passes_stay_aligned,
        test_notebook.test_baselines_land_on_the_ruled_lines,
        test_notebook.test_pagination_is_by_line_count,
        test_notebook.test_wrapping_keeps_every_pass_in_step,
        test_notebook.test_size_is_chosen_to_fit_the_ruling_and_the_width,
        test_notebook.test_impossible_notebooks_are_refused,
        test_notebook.test_a_page_is_reproducible,
    ])
    print("=== console job runner ===\n")
    _harness.run([
        test_console.test_failed_page_lifts_the_pen,
        test_console.test_multi_page_waits_for_a_fresh_sheet,
        test_console.test_bounds_are_checked_before_a_real_run,
        test_console.test_uploaded_paths_are_confined_to_the_uploads_folder,
        test_console.test_page_order_finishes_each_page_before_turning,
        test_console.test_pen_order_takes_one_pen_through_the_whole_notebook,
        test_console.test_the_cost_of_each_order_is_reported,
    ])
    raise SystemExit(_harness.report())
