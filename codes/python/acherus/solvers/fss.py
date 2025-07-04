"""Fourier Split-Step (FSS) solver module."""

import numpy as np
from scipy.fft import fftfreq
from scipy.linalg import solve_banded
from scipy.sparse import diags_array

from ..mathematics.routines.density import compute_density, compute_density_rk4
from ..mathematics.routines.nonlinear import compute_nonlinear_rk4
from ..mathematics.routines.raman import compute_raman, compute_raman_rk4
from ..mathematics.shared.fluence import compute_fluence
from ..mathematics.shared.fourier import compute_fft, compute_ifft
from ..mathematics.shared.intensity import compute_intensity
from ..mathematics.shared.radius import compute_radius
from ..physics.ionization import compute_ionization
from .base import SolverBase


class SolverFSS(SolverBase):
    """Fourier Split-Step class implementation for cylindrical coordinates."""

    def __init__(
        self,
        material,
        laser,
        grid,
        eqn,
        method_d_opt="RK4",
        method_r_opt="RK4",
        method_nl_opt="RK4",
        ion_model="MPI",
    ):
        """Initialize FSS class.

        Parameters
        ----------
        material : object
            Contains the chosen medium parameters.
        laser : object
            Contains the laser input parameters.
        grid : object
            Contains the grid input parameters.
        eqn : object
            Contains the equation parameters.
        method_d_opt : str, default: "RK4"
            Density solver method chosen.
        method_r_opt : str, default: "RK4"
            Raman solver method chosen.
        method_nl_opt : str, default: "RK4"
            Nonlinear solver method chosen.
        ion_model : str, default: "MPI"
            Ionization model chosen.

        """
        # Initialize base class
        super().__init__(
            material,
            laser,
            grid,
            eqn,
            method_d_opt,
            method_r_opt,
            method_nl_opt,
            ion_model,
        )

        # Initialize FSS-specific arrays
        self.envelope_split_rt = np.empty_like(self.envelope_rt)

        # Set initial conditions and equation operators
        self.set_initial_conditions()
        self.set_operators()

    def compute_matrix(self, n_r, m_p, coef_d):
        """
        Compute the three diagonals for the Crank-Nicolson array
        with centered differences.

        Parameters
        ----------
        n_r : integer
            Number of radial nodes.
        m_p : str
            Position of the Crank-Nicolson array ("left" or "right").
        coef_d : complex
            Coefficient for the diagonal elements.

        Returns
        -------
        lres : (3, M) ndarray
            Banded array for solving a large tridiagonal system.
        rres : sparse array
            Sparse array in CSR format for optimal matrix-vector product.
        """
        coef_main = 1 + 2 * coef_d
        r_ind = np.arange(1, n_r - 1)

        diag_lower = -coef_d * (1 - 0.5 / r_ind)
        diag_main = np.full(n_r, coef_main)
        diag_upper = -coef_d * (1 + 0.5 / r_ind)

        diag_lower = np.append(diag_lower, [0])
        diag_upper = np.insert(diag_upper, 0, [0])
        if m_p == "left":
            # Boundary conditions for the left matrix
            diag_main[0], diag_main[-1] = coef_main, 1
            diag_upper[0] = -2 * coef_d

            band_matrix = np.zeros((3, n_r), dtype=np.complex128)
            band_matrix[0, 1:] = diag_upper
            band_matrix[1, :] = diag_main
            band_matrix[2, :-1] = diag_lower

            # For the left hand side matrix, which will be used for
            # solving a large tridiagonal system of linear equations, return the
            # diagonals for latter usage in the banded solver
            return band_matrix

        if m_p == "right":
            # Boundary conditions for the right matrix
            diag_main[0], diag_main[-1] = coef_main, 0
            diag_upper[0] = -2 * coef_d

        diags = [diag_lower, diag_main, diag_upper]
        diags_ind = [-1, 0, 1]

        # For the right hand side matrix, which will be used for
        # computing a matrix-vector product, return the 'DIA' format
        # for tridiagonal matrices which is more efficient
        return diags_array(diags, offsets=diags_ind, format="dia")

    def set_operators(self):
        """Set FSS operators."""
        diff_c = 0.25 * self.z_res / (self.k_n * self.r_res**2)
        disp_c = -0.25 * self.z_res * self.k_pp / self.t_res**2

        # Set FFT exponential for dispersion
        w_grid = 2 * np.pi * fftfreq(self.t_nodes, self.t_res)
        self.disp_exp = np.exp(-2j * disp_c * (w_grid * self.t_res) ** 2)

        # Set Crank-Nicolson operators for diffraction
        self.matrix_left = self.compute_matrix(self.r_nodes, "left", 1j * diff_c)
        self.matrix_right = self.compute_matrix(self.r_nodes, "right", -1j * diff_c)

    def compute_dispersion(self):
        """
        Compute one step of the FFT propagation scheme for dispersion.
        """
        self.envelope_split_rt[:-1, :] = compute_fft(
            self.disp_exp * compute_ifft(self.envelope_rt[:-1, :]),
        )

    def compute_envelope(self):
        """
        Compute one step of the generalized Crank-Nicolson scheme
        for envelope propagation.
        """
        # Compute matrix-vector product using "DIA" sparse format
        rhs_linear = self.matrix_right @ self.envelope_split_rt

        # Compute the left-hand side of the equation
        rhs = rhs_linear + self.nonlinear_rt

        # Solve the tridiagonal system using the banded solver
        self.envelope_next_rt[:] = solve_banded((1, 1), self.matrix_left, rhs)

    def solve_step(self):
        """Perform one propagation step."""
        intensity_f = compute_intensity(
            self.envelope_rt[:-1, :],
            self.intensity_rt[:-1, :],
            self.r_grid[:-1],
            self.t_grid,
        )
        compute_ionization(
            self.intensity_rt[:-1, :],
            self.ionization_rate[:-1, :],
            self.ionization_sum[:-1, :],
            self.number_photons,
            self.hydrogen_f0,
            self.hydrogen_nc,
            self.keldysh_c,
            self.index_c,
            self.ppt_c,
            self.mpi_c,
            ion_model=self.ion_model,
            tol=1e-4,
        )
        if self.method_d == "RK4":
            compute_density_rk4(
                self.intensity_rt[:-1, :],
                self.density_rt[:-1, :],
                self.ionization_rate[:-1, :],
                self.t_grid,
                self.density_n,
                self.density_ini,
                self.avalanche_c,
            )
        else:
            compute_density(
                intensity_f,
                self.density_rt[:-1, :],
                self.ionization_rate[:-1, :],
                self.r_grid[:-1],
                self.t_grid,
                self.density_n,
                self.density_ini,
                self.avalanche_c,
                self.method_d,
            )
        if self.use_raman:
            if self.method_r == "RK4":
                compute_raman_rk4(
                    self.raman_rt[:-1, :],
                    self.draman_rt[:-1, :],
                    self.intensity_rt[:-1, :],
                    self.t_grid,
                    self.raman_c1,
                    self.raman_c2,
                )
            else:
                compute_raman(
                    self.raman_rt[:-1, :],
                    intensity_f,
                    self.r_grid[:-1],
                    self.t_grid,
                    self.raman_c1,
                    self.raman_c2,
                    self.method_r,
                )
        else:
            self.raman_rt.fill(0.0)
        self.compute_dispersion()
        if self.method_nl == "RK4":
            compute_nonlinear_rk4(
                self.envelope_split_rt[:-1, :],
                self.density_rt[:-1, :],
                self.raman_rt[:-1, :],
                self.ionization_rate[:-1, :],
                self.nonlinear_rt[:-1, :],
                self.density_n,
                self.plasma_c,
                self.mpa_c,
                self.kerr_c,
                self.raman_c,
                self.z_res,
            )
        self.compute_envelope()
        compute_fluence(self.envelope_next_rt[:-1, :], self.t_grid, self.fluence_r[:-1])
        compute_radius(self.fluence_r[:-1], self.r_grid[:-1], self.radius)

        self.envelope_rt[:], self.envelope_next_rt[:] = (
            self.envelope_next_rt,
            self.envelope_rt,
        )
