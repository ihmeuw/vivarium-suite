from pathlib import Path

from vivarium_profiling.tools.run_profile import (
    run_profile_cprofile,
    run_profile_scalene,
)


def test_run_profile_scalene_uses_correct_context_manager(mocker):
    """Test that run_profile_scalene uses scalene's enable_profiling context manager."""
    mock_sim = mocker.MagicMock()

    mock_context_manager = mocker.MagicMock()
    mock_enable_profiling = mocker.patch("scalene.scalene_profiler.enable_profiling")
    mock_enable_profiling.return_value = mock_context_manager

    run_profile_scalene(mock_sim)
    # Verify scalene's enable_profiling context manager was used
    mock_enable_profiling.assert_called_once()
    mock_context_manager.__enter__.assert_called_once()
    mock_context_manager.__exit__.assert_called_once()
    # Verify simulation was run
    mock_sim.run_simulation.assert_called_once()


def test_run_profile_cprofile_uses_correct_context_manager(mocker, tmp_path: Path):
    """Test that run_profile_cprofile uses cProfile.Profile context manager."""
    mock_sim = mocker.MagicMock()

    mock_profiler = mocker.MagicMock()
    mock_profile_class = mocker.patch("cProfile.Profile")
    mock_profile_class.return_value = mock_profiler

    # The context manager should return the same profiler instance
    mock_profiler.__enter__.return_value = mock_profiler

    output_file = str(tmp_path / "output.stats")

    run_profile_cprofile(mock_sim, output_file)

    # Verify cProfile.Profile context manager was used
    mock_profile_class.assert_called_once()
    mock_profiler.__enter__.assert_called_once()
    mock_profiler.__exit__.assert_called_once()
    # Verify simulation was run
    mock_sim.run_simulation.assert_called_once()
    # Verify stats were dumped to output file
    mock_profiler.dump_stats.assert_called_once_with(output_file)
