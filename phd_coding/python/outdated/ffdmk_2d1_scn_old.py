"""
This program solves the Unidirectional Pulse Propagation Equation (UPPE) of an ultra-intense
and ultra-short laser pulse in cylindrical coordinates with radial symmetry.
This program includes:
    - Diffraction (for the transverse direction).
    - Second order group velocity dispersion (GVD).
    - Multiphotonic ionization by multiphoton absorption (MPA).
    - Nonlinear optical Kerr effect (for a third-order centrosymmetric medium).

Numerical discretization: Finite Differences Method (FDM).
    - Method: Spectral-extended (in frequency) Crank-Nicolson (CN-AB2) scheme.
    - Initial condition: Gaussian.
    - Boundary conditions: Neumann-Dirichlet (radial) and Periodic (temporal).

UPPE:          ∂E/∂z = i/(2k) ∇²E - ik''/2 ∂²E/∂t² - iB_K|E|^(2K-2)E + ik_0n_2|E|^2 E 

DISCLAIMER: UPPE uses "god-like" units, where envelope intensity and its square module are the same.
            This is equivalent to setting 0.5*c*e_0*n_0 = 1 in the UPPE when using the SI system.
            The result obtained is identical since the consistency is mantained throught the code.
            This way, the number of operations is reduced, and the code is more readable.
            However, the dictionary "MEDIA" has an entry "INT_FACTOR" where the conversion 
            factor can be changed at will between the two unit systems.

E: envelope.
i: imaginary unit.
r: radial coordinate.
z: distance coordinate.
t: time coordinate.
k: wavenumber (in the interacting media).
k'': GVD coefficient of 2nd order.
k_0: wavenumber (in vacuum).
n_2: nonlinear refractive index (for a third-order centrosymmetric medium).
B_K: nonlinear multiphoton absorption coefficient.
∇: nabla operator (for the tranverse direction).
∇²: laplace operator (for the transverse direction).
"""

import numpy as np
from numpy.fft import fft, ifft
from scipy.sparse import diags_array
from scipy.sparse.linalg import spsolve
from tqdm import tqdm


def initial_condition(r, t, im, beam):
    """
    Set the post-lens chirped Gaussian beam.

    Parameters:
    - r (array): radial array
    - t (array): time array
    - im (complex): square root of -1
    - beam (dict): dictionary containing the beam parameters
        - a (float): amplitude of the Gaussian beam
        - w (float): waist of the Gaussian beam
        - wn (float): wavenumber of the Gaussian beam
        - f (float): focal length of the initial lens
        - pt (float): time at which the Gaussian beam reaches its peak intensity
        - ch (float): initial chirping introduced by some optical system
    """
    a = beam["AMPLITUDE"]
    w = beam["WAIST_0"]
    wn = beam["WAVENUMBER"]
    f = beam["FOCAL_LENGTH"]
    pt = beam["PEAK_TIME"]
    ch = beam["CHIRP"]
    gaussian = a * np.exp(
        -((r / w) ** 2) - 0.5 * im * wn * r**2 / f - (1 + im * ch) * (t / pt) ** 2
    )

    return gaussian


def crank_nicolson_diags(n, lr, c):
    """
    Set the three diagonals for the Crank-Nicolson array with centered differences.

    Parameters:
    - n (int): number of radial nodes
    - lr (str): position of the Crank-Nicolson array (left or right)
    - c (float): coefficient for the diagonal elements

    Returns:
    - tuple: upper, main, and lower diagonals
    """
    ind = np.arange(1, n - 1)

    diag_m1 = -c * (1 - 0.5 / ind)
    diag_0 = np.ones(n)
    diag_p1 = -c * (1 + 0.5 / ind)

    diag_m1 = np.append(diag_m1, [0])
    diag_p1 = np.insert(diag_p1, 0, [0])
    if lr == "LEFT":
        diag_p1[0] = -2 * c
    else:
        diag_p1[0] = -2 * c

    return diag_m1, diag_0, diag_p1


def crank_nicolson_array(n, lr, c):
    """
    Set the Crank-Nicolson sparse array in CSR format using the diagonals.

    Parameters:
    - n (int): number of radial nodes
    - lr (str): position of the Crank-Nicolson array (left or right)
    - c (float): coefficient for the diagonal elements

    Returns:
    - array: Crank-Nicolson sparse array in CSR format
    """
    diag_m1, diag_0, diag_p1 = crank_nicolson_diags(n, lr, c)

    diags = [diag_m1, diag_0, diag_p1]
    offset = [-1, 0, 1]
    matrix = diags_array(diags, offsets=offset, format="csr")

    return matrix


def nonlinear_terms(e_c, b, media):
    """
    Set the terms for nonlinear contributions.

    Parameters:
    - e_c: pre-allocated array for envelope at step k
    - b: pre-allocated array for intermediate results
    - media: dictionary with media parameters
    """
    abs_e_c = np.abs(e_c)
    b[:, :, 0] = e_c
    b[:, :, 1] = abs_e_c**2
    b[:, :, 2] = abs_e_c ** media["MPA_EXP"]


def adam_bashforth_step(b, w_c, media):
    """
    Compute one step of the Adam-Bashforth scheme for the nonlinear terms.

    Parameters:
    - b: pre-allocated array for intermediate results
    - w_c: pre-allocated array for Adam-Bashforth terms
    - media: dictionary with media parameters
    Compute one step of the Adam-Bashforth scheme for the nonlinear terms.
    """
    w_c[:] = (media["KERR_COEF"] * b[:, :, 1] + media["MPA_COEF"] * b[:, :, 2]) * b[
        :, :, 0
    ]


def fft_algorithm(e_c, fe_c, w_c, w_n, b):
    """
    Compute the FFT of the envelope and Adam-Bashforth terms.

    Parameters:
    - e_c: envelope at step k
    - fe_c: pre-allocated array for Fourier envelope at step k
    - w_c: current step nonlinear terms
    - w_n: previous step nonlinear terms
    - b: pre-allocated array for temporary results
    """
    fe_c[:] = fft(e_c, axis=1)
    b = fft(w_c[:, :], axis=1)
    w_c[:, :] = b
    b = fft(w_n[:, :], axis=1)
    w_n[:, :] = b


def crank_nicolson_step(mats, arr, cffs):
    """
    Update Crank-Nicolson arrays for one frquency step.
    Compute one step of the Crank-Nicolson propagation scheme.

    Parameters:
    - mats: dict containing sparse arrays
    - arr: dict containing intermediate arrays
    - cffs: dict containing sparse array diagonal coefficients
    """
    for l in range(arr["fe_c"].shape[1]):
        # Update matrices for current frequency
        mats["lm"].setdiag(cffs["left"][l])
        mats["rm"].setdiag(cffs["right"][l])
        # Set boundary conditions
        mats["lm"].data[-1] = 1
        mats["rm"].data[-1] = 0
        # Solve with Crank-Nicolson for current frequency
        arr["c"] = mats["rm"] @ arr["fe_c"][:, l]
        arr["d"] = arr["c"] + 1.5 * arr["w_c"][:, l] - 0.5 * arr["w_n"][:, l]
        arr["fe_n"][:, l] = spsolve(mats["lm"], arr["d"])


def ifft_algorithm(fe_c, e_c):
    """
    Compute the IFFT of the Fourier envelope at step k.

    Parameters:
    - fe_c: envelope in Fourier domain
    - e_c: pre-allocated array for envelope
    """
    e_c[:] = ifft(fe_c, axis=1)


IM_UNIT = 1j
PI = np.pi

## Set parameters (grid spacing, propagation step, etc.)
# Radial (r) grid
INI_RADI_COOR, FIN_RADI_COOR, I_RADI_NODES = 0, 25e-4, 1500
N_RADI_NODES = I_RADI_NODES + 2
RADI_STEP_LEN = (FIN_RADI_COOR - INI_RADI_COOR) / (N_RADI_NODES - 1)
AXIS_NODE = int(-INI_RADI_COOR / RADI_STEP_LEN)  # On-axis node
# Propagation (z) grid
INI_DIST_COOR, FIN_DIST_COOR, N_STEPS = 0, 3e-2, 1000
DIST_STEP_LEN = (FIN_DIST_COOR - INI_DIST_COOR) / N_STEPS
# Time (t) grid
INI_TIME_COOR, FIN_TIME_COOR, N_TIME_NODES = -200e-15, 200e-15, 8192
TIME_STEP_LEN = (FIN_TIME_COOR - INI_TIME_COOR) / (N_TIME_NODES - 1)
PEAK_NODE = N_TIME_NODES // 2  # Peak intensity node
# Angular frequency (ω) grid
FRQ_STEP_LEN = 2 * PI / (N_TIME_NODES * TIME_STEP_LEN)
INI_FRQ_COOR_W1 = 0
FIN_FRQ_COOR_W1 = PI / TIME_STEP_LEN - FRQ_STEP_LEN
INI_FRQ_COOR_W2 = -PI / TIME_STEP_LEN
FIN_FRQ_COOR_W2 = -FRQ_STEP_LEN
w1 = np.linspace(INI_FRQ_COOR_W1, FIN_FRQ_COOR_W1, N_TIME_NODES // 2)
w2 = np.linspace(INI_FRQ_COOR_W2, FIN_FRQ_COOR_W2, N_TIME_NODES // 2)
radi_array = np.linspace(INI_RADI_COOR, FIN_RADI_COOR, N_RADI_NODES)
dist_array = np.linspace(INI_DIST_COOR, FIN_DIST_COOR, N_STEPS + 1)
time_array = np.linspace(INI_TIME_COOR, FIN_TIME_COOR, N_TIME_NODES)
frq_array = np.append(w1, w2)
radi_2d_array, time_2d_array = np.meshgrid(radi_array, time_array, indexing="ij")

## Set beam and media parameters
LIGHT_SPEED = 299792458
PERMITTIVITY = 8.8541878128e-12
LIN_REF_IND_WATER = 1.328
NLIN_REF_IND_WATER = 1.6e-20
GVD_COEF_WATER = 241e-28
N_PHOTONS_WATER = 5
CS_MPA_WATER = 8e-64

WAVELENGTH_0 = 800e-9
WAIST_0 = 100e-6
PEAK_TIME = 50e-15
ENERGY = 2.83e-6
FOCAL_LENGTH = 20
CHIRP = -1

# INT_FACTOR = 0.5 * LIGHT_SPEED * PERMITTIVITY * LIN_REF_IND_WATER
INT_FACTOR = 1
WAVENUMBER_0 = 2 * PI / WAVELENGTH_0
WAVENUMBER = 2 * PI * LIN_REF_IND_WATER / WAVELENGTH_0
POWER = ENERGY / (PEAK_TIME * np.sqrt(0.5 * PI))
CR_POWER = 3.77 * WAVELENGTH_0**2 / (8 * PI * LIN_REF_IND_WATER * NLIN_REF_IND_WATER)
INTENSITY = 2 * POWER / (PI * WAIST_0**2)
AMPLITUDE = np.sqrt(INTENSITY / INT_FACTOR)

MPA_EXP = 2 * N_PHOTONS_WATER - 2
KERR_COEF = IM_UNIT * WAVENUMBER_0 * NLIN_REF_IND_WATER * DIST_STEP_LEN * INT_FACTOR
MPA_COEF = -0.5 * CS_MPA_WATER * DIST_STEP_LEN * INT_FACTOR ** (N_PHOTONS_WATER - 1)

## Set dictionaries for better organization
MEDIA = {
    "WATER": {
        "LIN_REF_IND": LIN_REF_IND_WATER,
        "NLIN_REF_IND": NLIN_REF_IND_WATER,
        "GVD_COEF": GVD_COEF_WATER,
        "N_PHOTONS": N_PHOTONS_WATER,  # Number of photons absorbed [-]
        "CS_MPA": CS_MPA_WATER,  # K-photon MPA coefficient [m(2K-3) - W-(K-1)]
        "MPA_EXP": MPA_EXP,  # MPA exponent [-]
        "MPA_COEF": MPA_COEF,  # MPA coefficient
        "KERR_COEF": KERR_COEF,  # Kerr coefficient
        "INT_FACTOR": INT_FACTOR,
    },
    "VACUUM": {
        "LIGHT_SPEED": LIGHT_SPEED,
        "PERMITTIVITY": PERMITTIVITY,
    },
}

## Set dictionaries for better organization
BEAM = {
    "WAVELENGTH_0": WAVELENGTH_0,
    "WAIST_0": WAIST_0,
    "PEAK_TIME": PEAK_TIME,
    "ENERGY": ENERGY,
    "FOCAL_LENGTH": FOCAL_LENGTH,
    "CHIRP": CHIRP,
    "WAVENUMBER_0": WAVENUMBER_0,
    "WAVENUMBER": WAVENUMBER,
    "POWER": POWER,
    "CR_POWER": CR_POWER,
    "INTENSITY": INTENSITY,
    "AMPLITUDE": AMPLITUDE,
}

## Set loop variables
DIST_INDEX = 0
DIST_LIMIT = 5
DELTA_R = 0.25 * DIST_STEP_LEN / (BEAM["WAVENUMBER"] * RADI_STEP_LEN**2)
DELTA_T = 0.25 * DIST_STEP_LEN * MEDIA["WATER"]["GVD_COEF"]
fourier_coeff = IM_UNIT * DELTA_T * frq_array**2
current_envelope = np.empty([N_RADI_NODES, N_TIME_NODES], dtype=complex)
next_envelope = np.empty_like(current_envelope)
fourier_envelope = np.empty_like(current_envelope)
dist_envelope = np.empty([N_RADI_NODES, DIST_LIMIT + 1, N_TIME_NODES], dtype=complex)
axis_envelope = np.empty([N_STEPS + 1, N_TIME_NODES], dtype=complex)
peak_envelope = np.empty([N_RADI_NODES, N_STEPS + 1], dtype=complex)
current_w_array = np.empty_like(current_envelope)
next_w_array = np.empty_like(current_envelope)
b_array = np.empty([N_RADI_NODES, N_TIME_NODES, 3], dtype=complex)
c_array = np.empty(N_RADI_NODES, dtype=complex)
d_array = np.empty_like(c_array)
f_array = np.empty_like(current_envelope)
k_indices = np.empty(DIST_LIMIT + 1, dtype=int)
temp_array = np.empty_like(current_envelope)

## Set tridiagonal Crank-Nicolson matrices in csr_array format
MATRIX_CNT_1 = IM_UNIT * DELTA_R
matrix_cnt_2 = 1 - 2 * MATRIX_CNT_1 + fourier_coeff
matrix_cnt_3 = 1 + 2 * MATRIX_CNT_1 - fourier_coeff
left_operator = crank_nicolson_array(N_RADI_NODES, "LEFT", MATRIX_CNT_1)
right_operator = crank_nicolson_array(N_RADI_NODES, "RIGHT", -MATRIX_CNT_1)

## Set initial electric field wave packet
current_envelope = initial_condition(radi_2d_array, time_2d_array, IM_UNIT, BEAM)
axis_envelope[0, :] = current_envelope[AXIS_NODE, :]

## Set dictionaries for better organization
operators = {"lm": left_operator, "rm": right_operator}
vectors = {
    "fe_c": fourier_envelope,
    "w_c": current_w_array,
    "w_n": next_w_array,
    "c": c_array,
    "d": d_array,
    "fe_n": f_array,
}
entries = {"left": matrix_cnt_3, "right": matrix_cnt_2}

## Propagation loop over desired number of steps (Spectral domain)
for k in tqdm(range(N_STEPS)):
    nonlinear_terms(current_envelope, b_array, MEDIA["WATER"])
    adam_bashforth_step(b_array, current_w_array, MEDIA["WATER"])
    if k == 0:
        next_w_array = current_w_array.copy()
        axis_envelope[k + 1, :] = current_envelope[AXIS_NODE, :]

    fft_algorithm(
        current_envelope, fourier_envelope, current_w_array, next_w_array, temp_array
    )
    crank_nicolson_step(operators, vectors, entries)
    ifft_algorithm(f_array, next_envelope)

    # Update arrays for the next step
    current_envelope, next_envelope = next_envelope, current_envelope
    next_w_array = current_w_array

    # Store data
    if (
        (k % (N_STEPS // DIST_LIMIT) == 0) or (k == N_STEPS - 1)
    ) and DIST_INDEX <= DIST_LIMIT:

        dist_envelope[:, DIST_INDEX, :] = current_envelope
        k_indices[DIST_INDEX] = k
        DIST_INDEX += 1

    # Store axis data
    if k > 0:
        axis_envelope[k + 1, :] = current_envelope[AXIS_NODE, :]
        peak_envelope[:, k + 1] = current_envelope[:, PEAK_NODE]

# Save to file
np.savez(
    "/Users/ytoga/projects/phd_thesis/phd_coding/python/storage/ffdmk_scn_1",
    e_dist=dist_envelope,
    e_axis=axis_envelope,
    e_peak=peak_envelope,
    k_indices=k_indices,
    INI_RADI_COOR=INI_RADI_COOR,
    FIN_RADI_COOR=FIN_RADI_COOR,
    INI_DIST_COOR=INI_DIST_COOR,
    FIN_DIST_COOR=FIN_DIST_COOR,
    INI_TIME_COOR=INI_TIME_COOR,
    FIN_TIME_COOR=FIN_TIME_COOR,
    AXIS_NODE=AXIS_NODE,
    PEAK_NODE=PEAK_NODE,
    LIN_REF_IND=MEDIA["WATER"]["LIN_REF_IND"],
)
