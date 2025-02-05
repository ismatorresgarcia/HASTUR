"""
This program solves the Unidirectional Pulse Propagation Equation (UPPE) of an ultra-intense
and ultra-short laser pulse.
This program includes:
    - Diffraction (for the transverse direction).
    - Second order group velocity dispersion (GVD).
    - Nonlinear optical Kerr effect (for a third-order centrosymmetric medium).
    - Multiphotonic ionization by multiphoton absorption (MPA).

Numerical discretization: Finite Differences Method (FDM)
    - Method: Extended Alternating Direction Implicit (ADI) scheme
        *- Alternating Direction Implicit (ADI) scheme (for diffraction and GVD)
        *- 2-step Adams-Bashforth (AB) scheme (for MPA and Kerr)
    - Initial condition: Gaussian
    - Boundary conditions: Neumann-Dirichlet (radial) and homogeneous Dirichlet (temporal)

UPPE:           ∂E/∂z = i/(2k) ∇²E - ik''/2 ∂²E/∂t² + ik_0n_2|E|^2 E - iB|E|^(2K-2)E

DISCLAIMER: UPPE uses "god-like" units, where envelope intensity and its square module are the same.
            This is equivalent to setting 0.5*c*e_0*n_0 = 1 in the UPPE when using the SI system.
            The result obtained is identical since the consistency is mantained throught the code.
            This way, the number of operations is reduced, and the code is more readable.

E: envelope
i: imaginary unit
r: radial coordinate
z: distance coordinate
t: time coordinate
k: wavenumber (in the interacting media)
k_0: wavenumber (in vacuum)
n_2: nonlinear refractive index (for a third-order centrosymmetric medium)
B: nonlinear multiphoton absorption coefficient
∇: nabla operator (for the tranverse direction)
∇²: laplace operator (for the transverse direction)
"""

import numpy as np
from scipy.sparse import diags_array
from scipy.sparse.linalg import spsolve
from tqdm import tqdm


def gaussian_beam(r, t, amplitude, waist, wavenumber, focal, peak_time, chirp):
    """
    Set the post-lens chirped Gaussian beam.

    Parameters:
    - r (array): Radial array
    - t (array): Time array
    - amplitude (float): Amplitude of the Gaussian beam
    - waist (float): Waist of the Gaussian beam
    - focal (float): Focal length of the initial lens
    - peak_time (float): Time at which the Gaussian beam reaches its peaks
    - chirp (float): Initial chirping introduced by some optical system
    """
    gaussian = amplitude * np.exp(
        -((r / waist) ** 2)
        - IMAG_UNIT * 0.5 * wavenumber * r**2 / focal
        - (1 + IMAG_UNIT * chirp) * (t / peak_time) ** 2
    )

    return gaussian


def crank_nicolson_diagonals_r(nodes, off_coeff, main_coeff, coor_system):
    """
    Set the three diagonals for a Crank-Nicolson radial array with centered differences.

    Parameters:
    - nodes (int): Number of radial nodes
    - off_coeff (float): Coefficient for the off-diagonal elements
    - main_coeff (float): Coefficient for the main diagonal elements
    - coor_system (int): Parameter for planar (0) or cylindrical (1) geometry

    Returns:
    - tuple: Containing the upper, main, and lower diagonals
    """
    indices = np.arange(1, nodes - 1)

    lower_diag = off_coeff * (1 - 0.5 * coor_system / indices)
    main_diag = np.full(nodes, main_coeff)
    upper_diag = off_coeff * (1 + 0.5 * coor_system / indices)
    lower_diag = np.append(lower_diag, [0])
    upper_diag = np.insert(upper_diag, 0, [0])

    return lower_diag, main_diag, upper_diag


def crank_nicolson_diagonals_t(nodes, off_coeff, main_coeff):
    """
    Set the three diagonals for a Crank-Nicolson time array with centered differences.

    Parameters:
    - nodes (int): Number of time nodes
    - off_coeff (float): Coefficient for the off-diagonal elements
    - main_coeff (float): Coefficient for the main diagonal elements

    Returns:
    - tuple: Containing the upper, main, and lower diagonals
    """
    lower_diag = np.full(nodes - 1, off_coeff)
    main_diag = np.full(nodes, main_coeff)
    upper_diag = np.full(nodes - 1, off_coeff)

    return lower_diag, main_diag, upper_diag


def crank_nicolson_array_r(nodes, off_coeff, main_coeff, coor_system):
    """
    Set the Crank-Nicolson radial sparse array in CSR format using the diagonals.

    Parameters:
    - nodes (int): Number of radial nodes
    - off_coeff (float): Coefficient for the off-diagonal elements
    - main_coeff (float): Coefficient for the main diagonal elements
    - coor_system (int): Parameter for planar (0) or cylindrical (1) geometry

    Returns:
    - array: Containing the Crank-Nicolson sparse array in CSR format
    """
    lower_diag, main_diag, upper_diag = crank_nicolson_diagonals_r(
        nodes, off_coeff, main_coeff, coor_system
    )

    diagonals = [lower_diag, main_diag, upper_diag]
    offset = [-1, 0, 1]
    array = diags_array(diagonals, offsets=offset, format="csr")

    return array


def crank_nicolson_array_t(nodes, off_coeff, main_coeff):
    """
    Set the Crank-Nicolson time sparse array in CSR format using the diagonals.

    Parameters:
    - nodes (int): Number of time nodes
    - off_coeff (float): Coefficient for the off-diagonal elements
    - main_coeff (float): Coefficient for the main diagonal elements

    Returns:
    - array: Containing the Crank-Nicolson sparse array in CSR format
    """
    lower_diag, main_diag, upper_diag = crank_nicolson_diagonals_t(
        nodes, off_coeff, main_coeff
    )

    diagonals = [lower_diag, main_diag, upper_diag]
    offset = [-1, 0, 1]
    array = diags_array(diagonals, offsets=offset, format="csr")

    return array


## Set physical and mathematical constants
IMAG_UNIT = 1j
PI_NUMBER = np.pi
ELEC_PERMITTIVITY_0 = 8.8541878128e-12
LIGHT_SPEED_0 = 299792458.0

## Set physical variables (for water at 800 nm)
BEAM_WLEN_0 = 800e-9
LINEAR_REFF = 1.334
NON_LINEAR_REFF = 4.1e-20
# GVD_COEFF = 0
GVD_COEFF = 241e-28  # 2nd order GVD coefficient [s2 / m]
PHOTON_NUMBER = 5  # Number of photons absorbed [-]
BETA_K = 1e-61  # MPA coefficient [m(2K-3) / W-(K-1)]
BEAM_WNUMBER_0 = 2 * PI_NUMBER / BEAM_WLEN_0
BEAM_WNUMBER = BEAM_WNUMBER_0 * LINEAR_REFF
# KERR_COEFF = 0
KERR_COEFF = IMAG_UNIT * BEAM_WNUMBER_0 * NON_LINEAR_REFF  # [m/W]
# MPA_COEFF = 0
MPA_COEFF = -0.5 * BETA_K ** (PHOTON_NUMBER - 1)  # [m(2K-3) / W-(K-1)]
MPA_EXPONENT = 2 * PHOTON_NUMBER - 2

## Set parameters (grid spacing, propagation step, etc.)
# Radial (r) grid
INI_RADI_COOR, FIN_RADI_COOR, I_RADI_NODES = 0.0, 75e-4, 200
N_RADI_NODES = I_RADI_NODES + 2
RADI_STEP_LEN = (FIN_RADI_COOR - INI_RADI_COOR) / (N_RADI_NODES - 1)
AXIS_NODE = int(-INI_RADI_COOR / RADI_STEP_LEN)  # On-axis node
# Propagation (z) grid
INI_DIST_COOR, FIN_DIST_COOR, N_STEPS = 0.0, 6e-2, 300
DIST_STEP_LEN = FIN_DIST_COOR / N_STEPS
# Time (t) grid
INI_TIME_COOR, FIN_TIME_COOR, I_TIME_NODES = -300e-15, 300e-15, 1024
N_TIME_NODES = I_TIME_NODES + 2
TIME_STEP_LEN = (FIN_TIME_COOR - INI_TIME_COOR) / (N_TIME_NODES - 1)
PEAK_NODE = N_TIME_NODES // 2  # Peak intensity node
radi_array = np.linspace(INI_RADI_COOR, FIN_RADI_COOR, N_RADI_NODES)
dist_array = np.linspace(INI_DIST_COOR, FIN_DIST_COOR, N_STEPS + 1)
time_array = np.linspace(INI_TIME_COOR, FIN_TIME_COOR, N_TIME_NODES)
radi_2d_array, dist_2d_array = np.meshgrid(radi_array, dist_array, indexing="ij")
radi_2d_array_2, time_2d_array_2 = np.meshgrid(radi_array, time_array, indexing="ij")
dist_2d_array_3, time_2d_array_3 = np.meshgrid(dist_array, time_array, indexing="ij")

## Set loop variables
EU_CYL = 1  # Parameter for planar (0) or cylindrical (1) geometry
DELTA_R = 0.25 * DIST_STEP_LEN / (BEAM_WNUMBER * RADI_STEP_LEN**2)
DELTA_T = -0.25 * DIST_STEP_LEN * GVD_COEFF / TIME_STEP_LEN**2
envelope = np.empty_like(radi_2d_array_2, dtype=complex)
envelope_axis = np.empty_like(dist_2d_array_3, dtype=complex)
envelope_store = np.empty_like(envelope)
b_array = np.empty_like(envelope)
c_array = np.empty_like(envelope)
d_array = np.empty([N_RADI_NODES, N_TIME_NODES, 3], dtype=complex)
f_array = np.empty_like(envelope)
w_array = np.empty([N_RADI_NODES, N_TIME_NODES, 2], dtype=complex)

## Set tridiagonal Crank-Nicolson matrices in csr_array format
MATRIX_CNT_1R = IMAG_UNIT * DELTA_R
MATRIX_CNT_1T = IMAG_UNIT * DELTA_T
left_cn_matrix_r = crank_nicolson_array_r(
    N_RADI_NODES, -MATRIX_CNT_1R, 1 + 2 * MATRIX_CNT_1R, EU_CYL
)
right_cn_matrix_r = crank_nicolson_array_r(
    N_RADI_NODES, MATRIX_CNT_1R, 1 - 2 * MATRIX_CNT_1R, EU_CYL
)
left_cn_matrix_t = crank_nicolson_array_t(
    N_TIME_NODES, -MATRIX_CNT_1T, 1 + 2 * MATRIX_CNT_1T
)
right_cn_matrix_t = crank_nicolson_array_t(
    N_TIME_NODES, MATRIX_CNT_1T, 1 - 2 * MATRIX_CNT_1T
)

# Convert to lil_array (dia_array does not support slicing) class to manipulate BCs easier
left_cn_matrix_r = left_cn_matrix_r.tolil()
right_cn_matrix_r = right_cn_matrix_r.tolil()
left_cn_matrix_t = left_cn_matrix_t.tolil()
right_cn_matrix_t = right_cn_matrix_t.tolil()

# Set boundary conditions
# Radial array
if EU_CYL == 0:  # (Dirichlet type)
    left_cn_matrix_r[0, 0], right_cn_matrix_r[0, 0] = 1, 0
    left_cn_matrix_r[0, 1], right_cn_matrix_r[0, 1] = 0, 0
    left_cn_matrix_r[-1, -1], right_cn_matrix_r[-1, -1] = 1, 0
else:  # (Neumann-Dirichlet type)
    right_cn_matrix_r[0, 0] = 1 - 2 * MATRIX_CNT_1R
    left_cn_matrix_r[0, 0] = 1 + 2 * MATRIX_CNT_1R
    right_cn_matrix_r[0, 1] = 2 * MATRIX_CNT_1R
    left_cn_matrix_r[0, 1] = -2 * MATRIX_CNT_1R
    right_cn_matrix_r[-1, -1] = 0
    left_cn_matrix_r[-1, -1] = 1
# Temporal array
left_cn_matrix_t[0, 0], right_cn_matrix_t[0, 0] = 1, 0
left_cn_matrix_t[0, 1], right_cn_matrix_t[0, 1] = 0, 0
left_cn_matrix_t[-1, -1], right_cn_matrix_t[-1, -1] = 1, 0

## Convert to csr_array class (better for conversion from lil_array class) to perform operations
left_cn_matrix_r = left_cn_matrix_r.tocsr()
right_cn_matrix_r = right_cn_matrix_r.tocsr()
left_cn_matrix_t = left_cn_matrix_t.tocsr()
right_cn_matrix_t = right_cn_matrix_t.tocsr()

## Set electric field wave packet
BEAM_WAIST_0 = 75e-6
BEAM_PEAK_TIME = 130e-15
BEAM_ENERGY = 2.2e-6
BEAM_CHIRP = -10
FOCAL_LEN = 20
BEAM_CR_POWER = 3.77 * BEAM_WLEN_0**2 / (8 * PI_NUMBER * NON_LINEAR_REFF * LINEAR_REFF)
BEAM_POWER = BEAM_ENERGY / (BEAM_PEAK_TIME * np.sqrt(0.5 * PI_NUMBER))
BEAM_INTENSITY = 2 * BEAM_POWER / (PI_NUMBER * BEAM_WAIST_0**2)
BEAM_AMPLITUDE = np.sqrt(BEAM_INTENSITY)
# Wave packet's initial condition
envelope = gaussian_beam(
    radi_2d_array_2,
    time_2d_array_2,
    BEAM_AMPLITUDE,
    BEAM_WAIST_0,
    BEAM_WNUMBER,
    FOCAL_LEN,
    BEAM_PEAK_TIME,
    BEAM_CHIRP,
)
envelope_axis[0, :] = envelope[AXIS_NODE, :]  # Save on-axis envelope initial state

## Propagation loop over desired number of steps
for k in tqdm(range(N_STEPS)):
    ## Compute first half-step (ADI transverse direction)
    # Compute right-hand side matrix product row by row
    for i in range(N_RADI_NODES):
        b_array[i, :] = right_cn_matrix_t @ envelope[i, :]

    # Compute first half-step solution
    for l in range(N_TIME_NODES):
        d_array[:, l, 0] = envelope[:, l]
        d_array[:, l, 1] = np.abs(d_array[:, l, 0]) ** 2
        d_array[:, l, 2] = np.abs(d_array[:, l, 0]) ** MPA_EXPONENT
        if k == 0:  # I'm guessing a value for starting the AB2 method
            w_array[:, l, 0] = (
                DIST_STEP_LEN
                * (KERR_COEFF * d_array[:, l, 1] + MPA_COEFF * d_array[:, l, 2])
                * d_array[:, l, 0]
            )
            G = 1.0
            d_array[:, l, 0] = G * d_array[:, l, 0]
            d_array[:, l, 1] = np.abs(d_array[:, l, 0]) ** 2
            d_array[:, l, 2] = np.abs(d_array[:, l, 0]) ** MPA_EXPONENT
            w_array[:, l, 1] = (
                DIST_STEP_LEN
                * (KERR_COEFF * d_array[:, l, 1] + MPA_COEFF * d_array[:, l, 2])
                * d_array[:, l, 0]
            )
            envelope_axis[k + 1, l] = d_array[
                AXIS_NODE, l, 0
            ]  # Save on-axis envelope 1-step
        else:
            w_array[:, l, 1] = (
                DIST_STEP_LEN
                * (KERR_COEFF * d_array[:, l, 1] + MPA_COEFF * d_array[:, l, 2])
                * d_array[:, l, 0]
            )

        # Compute intermediate arrays
        f_array[:, l] = b_array[:, l] + 0.25 * (3 * w_array[:, l, 1] - w_array[:, l, 0])

        # Compute first half-step solution
        c_array[:, l] = spsolve(left_cn_matrix_r, f_array[:, l])

    # Update arrays for the next half-step
    w_array[:, :, 0] = w_array[:, :, 1]

    ## Compute second half-step (ADI time direction)
    # Compute right-hand side matrix product column by column
    for l in range(N_TIME_NODES):
        b_array[:, l] = right_cn_matrix_r @ c_array[:, l]

    # Compute second half-step solution
    for i in range(N_RADI_NODES):
        d_array[i, :, 0] = c_array[i, :]
        d_array[i, :, 1] = np.abs(d_array[i, :, 0]) ** 2
        d_array[i, :, 2] = np.abs(d_array[i, :, 0]) ** MPA_EXPONENT
        if k == 0:  # I'm guessing a value for starting the AB2 method
            w_array[i, :, 0] = (
                DIST_STEP_LEN
                * (KERR_COEFF * d_array[i, :, 1] + MPA_COEFF * d_array[i, :, 2])
                * d_array[i, :, 0]
            )
            G = 1.0
            d_array[i, :, 0] = G * d_array[i, :, 0]
            d_array[i, :, 1] = np.abs(d_array[i, :, 0]) ** 2
            d_array[i, :, 2] = np.abs(d_array[i, :, 0]) ** MPA_EXPONENT
            if i == AXIS_NODE:
                envelope_axis[k + 1, :] = d_array[
                    i, :, 0
                ]  # Save on-axis envelope 1-step
        else:
            w_array[i, :, 1] = (
                DIST_STEP_LEN
                * (KERR_COEFF * d_array[i, :, 1] + MPA_COEFF * d_array[i, :, 2])
                * d_array[i, :, 0]
            )

        # Compute intermediate arrays
        f_array[i, :] = b_array[i, :] + 0.25 * (3 * w_array[i, :, 1] - w_array[i, :, 0])

        # Compute second half-step solution
        envelope_store[i, :] = spsolve(left_cn_matrix_t, f_array[i, :])

    # Update arrays for the next step
    w_array[:, :, 0] = w_array[:, :, 1]
    envelope = envelope_store
    envelope_axis[k + 1, :] = envelope_store[
        AXIS_NODE, :
    ]  # Save on-axis envelope k-step

np.savez(
    "/Users/ytoga/projects/phd_thesis/phd_coding/python/practitioners_guide/storage/ffdmk_adi2_1",
    INI_RADI_COOR=INI_RADI_COOR,
    FIN_RADI_COOR=FIN_RADI_COOR,
    INI_DIST_COOR=INI_DIST_COOR,
    FIN_DIST_COOR=FIN_DIST_COOR,
    INI_TIME_COOR=INI_TIME_COOR,
    FIN_TIME_COOR=FIN_TIME_COOR,
    AXIS_NODE=AXIS_NODE,
    PEAK_NODE=PEAK_NODE,
    LINEAR_REFF=LINEAR_REFF,
    e=envelope,
    e_axis=envelope_axis,
)
