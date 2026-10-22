use std::f64::consts::PI;

pub const SEG_MIN_SEP: f64 = 6.0;
pub const SEG_MIN_LEN: f64 = 5.0;
pub const PILL_PROX: f64 = SEG_MIN_SEP;
pub const BOUNDARY_MIN_SEP: f64 = 10.0;
pub const WIRE_MIN_SEP: f64 = 10.0;
pub const WIRE_APPROACH_SEP: f64 = 26.0;
pub const WIRE_PARALLEL_TOL: f64 = PI / 6.0;
pub const WIRE_ANCHOR_TRIM: f64 = 8.0;
pub const DOUBLE_CROSS_EXTRA_W: f64 = 1e11;

pub const BEVEL_STEP: f64 = 3.0;
pub const BEVEL_MAX: f64 = 45.0;
pub const BEVEL_MIN_APPLY: f64 = 6.0;
pub const JOG_STEP: f64 = 3.0;
pub const JOG_MAX: f64 = 24.0;
pub const JOG_MIN_APPLY: f64 = 6.0;
pub const OPT_MAX_PASSES: usize = 8;

pub const CORNER_MIN_DIST: f64 = 24.0;

pub const REFINE_MIN_SEP: f64 = 3.0;
pub const REFINE_FAN_STEP: f64 = 4.0;
pub const REFINE_MAX_OFFSET: f64 = 14.0;
pub const REFINE_MAX_PASSES: usize = 20;

pub const HUG_MARGIN: f64 = 6.0;
pub const HUG_LANE_STEP: f64 = 5.0;
pub const HUG_MAX_LANES: usize = 20;

pub const DIVE_MODES: [i32; 3] = [0, 1, 2];
pub const CHANNEL_Y_STEPS: [f64; 12] =
    [-24., -16., -8., -4., 4., 8., 16., 24., 32., 40., -32., -40.];
pub const OFFSET_A_STEPS: [f64; 18] =
    [-240., -200., -160., -120., -80., -48., -24., -8., -2.,
      2., 8., 24., 48., 80., 120., 160., 200., 240.];
pub const OFFSET_A_ABS: [f64; 19] =
    [-240., -200., -160., -120., -80., -48., -24., -16., -8., 0.,
      8., 16., 24., 48., 80., 120., 160., 200., 240.];
pub const DETOUR_BIAS_STEPS: [f64; 4] = [-40., -20., 20., 40.];

pub const LANE_CHOICES: [f64; 8] = [2., 6., 10., 14., 18., 22., 26., 30.];
pub const BIAS_CHOICES: [f64; 5] = [0., -40., -20., 20., 40.];
pub const BYSTANDER_RADIUS: f64 = 100.0;
pub const MAX_BYSTANDERS: usize = 4;
pub const MAX_PAIRS_2WAY: usize = 8;
pub const MAX_PAIRS_3WAY: usize = 4;
pub const PAIR_REFINE_ITERS: usize = 2;

pub const PUSH_STEPS: [f64; 16] =
    [4., -4., 8., -8., 12., -12., 16., -16.,
     20., -20., 24., -24., 28., -28., 32., -32.];
pub const PUSH_MAX_ROUNDS: usize = 3;
pub const PAIR_PUSH_SHIFTS: [f64; 7] = [0., -8., 8., -16., 16., -24., 24.];
pub const MAX_PUSH_PAIRS: usize = 4;

pub const HUG_GAP: f64 = 8.0;
pub const HUG_MIN_CHANNEL: f64 = 2.0;
pub const MAX_HUG_PAIRS: usize = 8;

pub const POLISH_MAX_ROUNDS: usize = 6;
