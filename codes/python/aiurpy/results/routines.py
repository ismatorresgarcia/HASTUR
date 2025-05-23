"""Diagnosing tools module."""

import pstats
import sys
from pstats import SortKey

import h5py
import numpy as np

from .paths import sim_dir as path

profiler_path = path / "profiler_log.txt"
temp_ofdiagnostic_path = path / "temp_ofdiagnostic.h5"
INT_MONITOR = 100


def validate_step(solver, exit_on_error=True):
    """
    Validate numerical results from solver state.

    Parameters
    ----------
    solver : object
        Solver object with data.
    exit_on_error : bool, default: True
        Whether to exit program on validation failure.

    Returns
    -------
    out : bool
        True if valid, False if invalid (when exit_on_error is False).

    """
    # Check envelope for non-finite values
    if np.any(~np.isfinite(solver.envelope_rt)):
        if exit_on_error:
            print("ERROR: Non-finite values detected in envelope")
            sys.exit(1)
        else:
            print("WARNING: Non-finite values detected in envelope")
            return False

    # Check density for non-finite values
    if np.any(~np.isfinite(solver.density_rt)):
        if exit_on_error:
            print("ERROR: Non-finite values detected in density")
            sys.exit(1)
        else:
            print("WARNING: Non-finite values detected in density")
            return False

    return True


def cheap_diagnostics(solver, step):
    """
    Save memory cheap diagnostics data for current step.

    Parameters
    ----------
    solver : object
        Solver object with data.
    step : integer
        Current propagation step.

    """
    # Validate current solver state
    validate_step(solver)

    envelope_rt = solver.envelope_rt
    density_rt = solver.density_rt

    axis_data_envelope = envelope_rt[0]
    axis_data_density = density_rt[0]
    axis_data_intensity = np.abs(axis_data_envelope)

    peak_node_intensity = np.argmax(axis_data_intensity)
    peak_node_density = np.argmax(axis_data_density)

    solver.envelope_r0_zt[step] = axis_data_envelope
    solver.envelope_tp_rz[:, step] = envelope_rt[:, peak_node_intensity]
    solver.density_r0_zt[step] = axis_data_density
    solver.density_tp_rz[:, step] = density_rt[:, peak_node_density]
    solver.fluence_rz[:, step] = solver.fluence_r
    solver.radius_z[step] = solver.radius[0]


def inter_diagnostics(solver, step):
    """
    Save diagnostics progressively every desired number of steps
    and write them in a HDF5 file on the run.

    Parameters
    ----------
    solver : object
        Solver object with data.
    step : integer
        Current propagation step.

    """
    if step == 1:
        with h5py.File(temp_ofdiagnostic_path, "w") as f:
            envelope_grp = f.create_group("envelope")
            envelope_grp.create_dataset(
                "peak_rz",
                shape=(solver.grid.r_nodes, solver.grid.zd.z_steps + 1),
                maxshape=(solver.grid.r_nodes, None),
                dtype=complex,
                compression="gzip",
                chunks=(
                    solver.grid.r_nodes,
                    min(INT_MONITOR, solver.grid.zd.z_steps + 1),
                ),
            )

            coords = f.create_group("coordinates")
            coords.create_dataset("r_min", data=solver.grid.rd.r_min)
            coords.create_dataset("r_max", data=solver.grid.rd.r_max)
            coords.create_dataset("z_min", data=solver.grid.zd.z_min)
            coords.create_dataset("z_max", data=solver.grid.zd.z_max)
            coords.create_dataset("r_grid", data=solver.grid.r_grid)
            coords.create_dataset("z_grid", data=solver.grid.z_grid)

            # Add metadata
            meta = f.create_group("metadata")
            meta.create_dataset("last_step", data=0, dtype=int)

    # Update data
    if step % INT_MONITOR == 0 or step == solver.grid.zd.z_steps:
        with h5py.File(temp_ofdiagnostic_path, "r+") as f:
            last_step = f["metadata/last_step"][()]

            if step > last_step:
                f["envelope/peak_rz"][:, last_step + 1 : step + 1] = (
                    solver.envelope_tp_rz[:, last_step + 1 : step + 1]
                )
                f["metadata/last_step"][()] = step


def expensive_diagnostics(solver, step):
    """
    Save memory expensive diagnostics data for current step.

    Parameters
    ----------
    solver : object
        Solver object with data.
    step : integer
        Current propagation step.

    """
    solver.envelope_snapshot_rzt[:, step, :] = solver.envelope_rt
    solver.density_snapshot_rzt[:, step, :] = solver.density_rt
    solver.snapshot_z_index[step] = (
        solver.snapshot_z_index[step - 1] + solver.grid.z_steps_per_snapshot
    )


def profiler_log(profiler, save_path=None, top_n=20):
    """
    Profiler analysis for the func execution and save a detailed report.

    Parameters
    ----------
    profiler : object
        cProfile.Profile object after disable() is called.
    save_path : str
        Path to save the report (default: PROFILER_PATH).
    top_n : integer, default: 20
        Top N time-consuming functions to display.

    Returns
    -------
    stats : object
        pstats.Stats object with profiling report.

    """
    prof_path = save_path if save_path else profiler_path

    # Generate the report
    with open(prof_path, "w", encoding="utf-8") as f:
        # Header
        f.write("=" * 80 + "\n")
        f.write("PERFORMANCE PROFILE REPORT\n")
        f.write("=" * 80 + "\n\n")

        # General statistics
        f.write("SUMMARY\n")
        f.write("-" * 80 + "\n")
        stats = pstats.Stats(profiler, stream=f)
        stats.strip_dirs().sort_stats().print_stats(0)

        # Most time-consuming functions
        f.write("\n\nTOP TIME-CONSUMING FUNCTIONS\n")
        f.write("-" * 80 + "\n")
        stats = pstats.Stats(profiler, stream=f)
        stats.strip_dirs().sort_stats(SortKey.TIME).print_stats(top_n)

        # Cumulative time statistics
        f.write("\n\nTOP TIME-ACCUMULATED FUNCTIONS\n")
        f.write("-" * 80 + "\n")
        stats = pstats.Stats(profiler, stream=f)
        stats.strip_dirs().sort_stats(SortKey.CUMULATIVE).print_stats(top_n)

        # Most frequent calls
        f.write("\n\nMOST FREQUENT CALLED FUNCTIONS\n")
        f.write("-" * 80 + "\n")
        stats = pstats.Stats(profiler, stream=f)
        stats.strip_dirs().sort_stats(SortKey.CALLS).print_stats(top_n)

        # Solvers analysis
        main_routines = [
            "compute_density",
            "compute_raman",
            "compute_dispersion",
            "compute_envelope",
            "compute_nlin_rk4",
            "compute_nlin_rk4_freq",
            "solve_step",
        ]

        for func_chr in main_routines:
            f.write(f"\n--- Analysis of {func_chr} ---\n")
            stats = pstats.Stats(profiler, stream=f)
            stats.strip_dirs()
            stats.print_callers(func_chr)
            stats.print_callees(func_chr)

        return pstats.Stats(profiler)
