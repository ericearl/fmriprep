# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""Parser."""
import os
from .. import config


def _build_parser():
    """Build parser object."""
    from functools import partial
    from pathlib import Path
    from argparse import (
        ArgumentParser,
        ArgumentDefaultsHelpFormatter,
    )
    from packaging.version import Version
    from .version import check_latest, is_flagged
    from niworkflows.utils.spaces import Reference, SpatialReferences, OutputReferencesAction

    def _path_exists(path, parser):
        """Ensure a given path exists."""
        if path is None or not Path(path).exists():
            raise parser.error("Path does not exist: <%s>." % path)
        return Path(path).absolute()

    def _min_one(value, parser):
        """Ensure an argument is not lower than 1."""
        value = int(value)
        if value < 1:
            raise parser.error("Argument can't be less than one.")
        return value

    def _to_gb(value):
        units = {"G": 1, "T": 10**3, "": 1e-3, "M": 1e-3}
        value = value.upper().rstrip("B")
        digits = ''.join([c for c in value if c.isdigit()])
        return int(digits) * units[value[len(digits):]]

    def _drop_sub(value):
        value = str(value)
        return value.lstrip('sub-')

    verstr = 'fMRIPrep v{}'.format(config.execution.version)
    currentv = Version(config.execution.version)
    is_release = not any((currentv.is_devrelease, currentv.is_prerelease, currentv.is_postrelease))

    parser = ArgumentParser(
        description='fMRIPrep: fMRI PREProcessing workflows v{}'.format(config.execution.version),
        formatter_class=ArgumentDefaultsHelpFormatter)
    PathExists = partial(_path_exists, parser=parser)
    PositiveInt = partial(_min_one, parser=parser)

    # Arguments as specified by BIDS-Apps
    # required, positional arguments
    # IMPORTANT: they must go directly with the parser object
    parser.add_argument('bids_dir', action='store', type=PathExists,
                        help='the root folder of a BIDS valid dataset (sub-XXXXX folders should '
                             'be found at the top level in this folder).')
    parser.add_argument('output_dir', action='store', type=Path,
                        help='the output path for the outcomes of preprocessing and visual '
                             'reports')
    parser.add_argument('analysis_level', choices=['participant'],
                        help='processing stage to be run, only "participant" in the case of '
                             'fMRIPrep (see BIDS-Apps specification).')

    # optional arguments
    parser.add_argument('--version', action='version', version=verstr)

    g_bids = parser.add_argument_group('Options for filtering BIDS queries')
    g_bids.add_argument('--skip_bids_validation', '--skip-bids-validation', action='store_true',
                        default=False,
                        help='assume the input dataset is BIDS compliant and skip the validation')
    g_bids.add_argument(
        '--participant-label', '--participant_label', action='store', nargs='+', type=_drop_sub,
        help='a space delimited list of participant identifiers or a single '
             'identifier (the sub- prefix can be removed)')
    # Re-enable when option is actually implemented
    # g_bids.add_argument('-s', '--session-id', action='store', default='single_session',
    #                     help='select a specific session to be processed')
    # Re-enable when option is actually implemented
    # g_bids.add_argument('-r', '--run-id', action='store', default='single_run',
    #                     help='select a specific run to be processed')
    g_bids.add_argument('-t', '--task-id', action='store',
                        help='select a specific task to be processed')
    g_bids.add_argument('--echo-idx', action='store', type=int,
                        help='select a specific echo to be processed in a multiecho series')

    g_perfm = parser.add_argument_group('Options to handle performance')
    g_perfm.add_argument(
        '--nprocs', '--nthreads', '--n_cpus', '-n-cpus', action='store', type=PositiveInt,
        help='maximum number of threads across all processes')
    g_perfm.add_argument('--omp-nthreads', action='store', type=PositiveInt,
                         help='maximum number of threads per-process')
    g_perfm.add_argument('--mem', '--mem-mb', dest='memory_gb', action='store', type=_to_gb,
                         help='upper bound memory limit for fMRIPrep processes')
    g_perfm.add_argument('--low-mem', action='store_true',
                         help='attempt to reduce memory usage (will increase disk usage '
                              'in working directory)')
    g_perfm.add_argument('--use-plugin', action='store', default=None,
                         help='nipype plugin configuration file')
    g_perfm.add_argument('--anat-only', action='store_true',
                         help='run anatomical workflows only')
    g_perfm.add_argument('--boilerplate_only', action='store_true', default=False,
                         help='generate boilerplate only')
    g_perfm.add_argument('--md-only-boilerplate', action='store_true',
                         default=False,
                         help='skip generation of HTML and LaTeX formatted citation with pandoc')
    g_perfm.add_argument('--error-on-aroma-warnings', action='store_true',
                         dest='aroma_err_on_warn', default=False,
                         help='Raise an error if ICA_AROMA does not produce sensible output '
                              '(e.g., if all the components are classified as signal or noise)')
    g_perfm.add_argument("-v", "--verbose", dest="verbose_count", action="count", default=0,
                         help="increases log verbosity for each occurence, debug level is -vvv")

    g_conf = parser.add_argument_group('Workflow configuration')
    g_conf.add_argument(
        '--ignore', required=False, action='store', nargs="+", default=[],
        choices=['fieldmaps', 'slicetiming', 'sbref'],
        help='ignore selected aspects of the input dataset to disable corresponding '
             'parts of the workflow (a space delimited list)')
    g_conf.add_argument(
        '--longitudinal', action='store_true',
        help='treat dataset as longitudinal - may increase runtime')
    g_conf.add_argument(
        '--t2s-coreg', action='store_true',
        help='If provided with multi-echo BOLD dataset, create T2*-map and perform '
             'T2*-driven coregistration. When multi-echo data is provided and this '
             'option is not enabled, standard EPI-T1 coregistration is performed '
             'using the middle echo.')
    g_conf.add_argument(
        '--output-spaces', nargs='+', action=OutputReferencesAction, default=SpatialReferences(),
        help="""\
Standard and non-standard spaces to resample anatomical and functional images to. \
Standard spaces may be specified by the form \
``<SPACE>[:cohort-<label>][:res-<resolution>][...]``, where ``<SPACE>`` is \
a keyword designating a spatial reference, and may be followed by optional, \
colon-separated parameters. \
Non-standard spaces imply specific orientations and sampling grids. \
Important to note, the ``res-*`` modifier does not define the resolution used for \
the spatial normalization.
For further details, please check out \
https://fmriprep.readthedocs.io/en/%s/spaces.html""" % (currentv.base_version
                                                        if is_release else 'latest'))

    g_conf.add_argument('--bold2t1w-dof', action='store', default=6, choices=[6, 9, 12], type=int,
                        help='Degrees of freedom when registering BOLD to T1w images. '
                             '6 degrees (rotation and translation) are used by default.')
    g_conf.add_argument(
        '--force-bbr', action='store_true', dest='use_bbr', default=None,
        help='Always use boundary-based registration (no goodness-of-fit checks)')
    g_conf.add_argument(
        '--force-no-bbr', action='store_false', dest='use_bbr', default=None,
        help='Do not use boundary-based registration (no goodness-of-fit checks)')
    g_conf.add_argument(
        '--medial-surface-nan', required=False, action='store_true', default=False,
        help='Replace medial wall values with NaNs on functional GIFTI files. Only '
        'performed for GIFTI files mapped to a freesurfer subject (fsaverage or fsnative).')
    g_conf.add_argument(
        '--dummy-scans', required=False, action='store', default=None, type=int,
        help='Number of non steady state volumes.')

    # ICA_AROMA options
    g_aroma = parser.add_argument_group('Specific options for running ICA_AROMA')
    g_aroma.add_argument('--use-aroma', action='store_true', default=False,
                         help='add ICA_AROMA to your preprocessing stream')
    g_aroma.add_argument('--aroma-melodic-dimensionality', dest='aroma_melodic_dim',
                         action='store', default=-200, type=int,
                         help='Exact or maximum number of MELODIC components to estimate '
                         '(positive = exact, negative = maximum)')

    # Confounds options
    g_confounds = parser.add_argument_group('Specific options for estimating confounds')
    g_confounds.add_argument(
        '--return-all-components', required=False, action='store_true', default=False,
        help='Include all components estimated in CompCor decomposition in the confounds '
             'file instead of only the components sufficient to explain 50 percent of '
             'BOLD variance in each CompCor mask')
    g_confounds.add_argument(
        '--fd-spike-threshold', required=False, action='store', default=0.5, type=float,
        help='Threshold for flagging a frame as an outlier on the basis of framewise '
             'displacement')
    g_confounds.add_argument(
        '--dvars-spike-threshold', required=False, action='store', default=1.5, type=float,
        help='Threshold for flagging a frame as an outlier on the basis of standardised '
             'DVARS')

    #  ANTs options
    g_ants = parser.add_argument_group('Specific options for ANTs registrations')
    g_ants.add_argument(
        '--skull-strip-template', default='OASIS30ANTs', type=Reference.from_string,
        help='select a template for skull-stripping with antsBrainExtraction')
    g_ants.add_argument('--skull-strip-fixed-seed', action='store_true',
                        help='do not use a random seed for skull-stripping - will ensure '
                             'run-to-run replicability when used with --omp-nthreads 1')

    # Fieldmap options
    g_fmap = parser.add_argument_group('Specific options for handling fieldmaps')
    g_fmap.add_argument('--fmap-bspline', action='store_true', default=False,
                        help='fit a B-Spline field using least-squares (experimental)')
    g_fmap.add_argument('--fmap-no-demean', action='store_false', default=True,
                        help='do not remove median (within mask) from fieldmap')

    # SyN-unwarp options
    g_syn = parser.add_argument_group('Specific options for SyN distortion correction')
    g_syn.add_argument('--use-syn-sdc', action='store_true', default=False,
                       help='EXPERIMENTAL: Use fieldmap-free distortion correction')
    g_syn.add_argument('--force-syn', action='store_true', default=False,
                       help='EXPERIMENTAL/TEMPORARY: Use SyN correction in addition to '
                       'fieldmap correction, if available')

    # FreeSurfer options
    g_fs = parser.add_argument_group('Specific options for FreeSurfer preprocessing')
    g_fs.add_argument(
        '--fs-license-file', metavar='PATH', type=PathExists,
        help='Path to FreeSurfer license key file. Get it (for free) by registering'
             ' at https://surfer.nmr.mgh.harvard.edu/registration.html')
    g_fs.add_argument(
        '--fs-subjects-dir', metavar='PATH', type=Path,
        help='Path to existing FreeSurfer subjects directory to reuse. '
             '(default: OUTPUT_DIR/freesurfer)')

    # Surface generation xor
    g_surfs = parser.add_argument_group('Surface preprocessing options')
    g_surfs.add_argument('--no-submm-recon', action='store_false', dest='hires',
                         help='disable sub-millimeter (hires) reconstruction')
    g_surfs_xor = g_surfs.add_mutually_exclusive_group()
    g_surfs_xor.add_argument('--cifti-output', nargs='?', const='91k', default=False,
                             choices=('91k', '170k'), type=str,
                             help='output preprocessed BOLD as a CIFTI dense timeseries. '
                             'Optionally, the number of grayordinate can be specified '
                             '(default is 91k, which equates to 2mm resolution)')
    g_surfs_xor.add_argument('--fs-no-reconall',
                             action='store_false', dest='run_reconall',
                             help='disable FreeSurfer surface preprocessing.')

    g_other = parser.add_argument_group('Other options')
    g_other.add_argument(
        '-w', '--work-dir', action='store', type=Path, default=Path('work').absolute(),
        help='path where intermediate results should be stored')
    g_other.add_argument('--clean-workdir', action='store_true', default=False,
                         help='Clears working directory of contents. Use of this flag is not'
                              'recommended when running concurrent processes of fMRIPrep.')
    g_other.add_argument(
        '--resource-monitor', action='store_true', default=False,
        help='enable Nipype\'s resource monitoring to keep track of memory and CPU usage')
    g_other.add_argument(
        '--reports-only', action='store_true', default=False,
        help='only generate reports, don\'t run workflows. This will only rerun report '
             'aggregation, not reportlet generation for specific nodes.')
    g_other.add_argument(
        '--run-uuid', action='store', default=None,
        help='Specify UUID of previous run, to include error logs in report. '
             'No effect without --reports-only.')
    g_other.add_argument('--write-graph', action='store_true', default=False,
                         help='Write workflow graph.')
    g_other.add_argument('--stop-on-first-crash', action='store_true', default=False,
                         help='Force stopping on first crash, even if a work directory'
                              ' was specified.')
    g_other.add_argument('--notrack', action='store_true', default=False,
                         help='Opt-out of sending tracking information of this run to '
                              'the FMRIPREP developers. This information helps to '
                              'improve FMRIPREP and provides an indicator of real '
                              'world usage crucial for obtaining funding.')
    g_other.add_argument('--sloppy', action='store_true', default=False,
                         help='Use low-quality tools for speed - TESTING ONLY')

    latest = check_latest()
    if latest is not None and currentv < latest:
        config.loggers.cli.warning("""\
You are using fMRIPrep-%s, and a newer version of fMRIPrep is available: %s.
Please check out our documentation about how and when to upgrade:
https://fmriprep.readthedocs.io/en/latest/faq.html#upgrading""" % (
            config.execution.version, latest))

    _blist = is_flagged()
    if _blist[0]:
        _reason = _blist[1] or 'unknown'
        config.loggers.cli.warning("""\
WARNING: Version %s of fMRIPrep (current) has been FLAGGED
(reason: %s).
That means some severe flaw was found in it and we strongly
discourage its usage.""" % (config.execution.version, _reason))

    return parser


def parse_args(args=None, namespace=None):
    """Parse args and run further checks on the command line."""
    import logging
    parser = _build_parser()
    opts = parser.parse_args(args, namespace)
    config.execution.log_level = int(max(25 - 5 * opts.verbose_count, logging.DEBUG))
    config.from_dict(vars(opts))

    config.set_logger_level()

    # Retrieve logging level
    build_log = config.loggers.cli

    if config.execution.fs_license_file is None:
        raise RuntimeError("""\
ERROR: a valid license file is required for FreeSurfer to run. fMRIPrep looked for an existing \
license file at several paths, in this order: 1) command line argument ``--fs-license-file``; \
2) ``$FS_LICENSE`` environment variable; and 3) the ``$FREESURFER_HOME/license.txt`` path. Get it \
(for free) by registering at https://surfer.nmr.mgh.harvard.edu/registration.html""")
    os.environ['FS_LICENSE'] = str(config.execution.fs_license_file)

    # Load base plugin_settings from file if --use-plugin
    if opts.use_plugin is not None:
        from yaml import load as loadyml
        with open(opts.use_plugin) as f:
            plugin_settings = loadyml(f)
        _plugin = plugin_settings.get('plugin')
        if _plugin:
            config.nipype.plugin = _plugin
            config.nipype.plugin_args = plugin_settings.get('plugin_args', {})
            config.nipype.nprocs = config.nipype.plugin_args.get('nprocs', config.nipype.nprocs)

    # Resource management options
    # Note that we're making strong assumptions about valid plugin args
    # This may need to be revisited if people try to use batch plugins
    if 1 < config.nipype.nprocs < config.nipype.omp_nthreads:
        build_log.warning(
            'Per-process threads (--omp-nthreads=%d) exceed total '
            'threads (--nthreads/--n_cpus=%d)', config.nipype.omp_nthread, config.nipype.nprocs)

    bids_dir = config.execution.bids_dir
    output_dir = config.execution.output_dir
    work_dir = config.execution.work_dir
    version = config.execution.version

    if config.execution.fs_subjects_dir is None:
        config.execution.fs_subjects_dir = output_dir / 'freesurfer'

    # Wipe out existing work_dir
    if opts.clean_workdir and work_dir.exists():
        from niworkflows.utils.misc import clean_directory
        build_log.log("Clearing previous fMRIPrep working directory: %s",
                      work_dir)
        if not clean_directory(work_dir):
            build_log.warning("Could not clear all contents of working directory: %s",
                              work_dir)

    # Ensure input and output folders are not the same
    if output_dir == bids_dir:
        config.loggers.cli.error(
            'The selected output folder is the same as the input BIDS folder. '
            'Please modify the output path (suggestion: %s).'
            % bids_dir / 'derivatives' / ('fmriprep-%s' % version.split('+')[0])
        )

    if bids_dir in work_dir.parents:
        config.loggers.cli.error(
            'The selected working directory is a subdirectory of the input BIDS folder. '
            'Please modify the output path.'
        )

    # Validate inputs
    if not opts.skip_bids_validation:
        from ..utils.bids import validate_input_dir
        build_log.info(
            "Making sure the input data is BIDS compliant (warnings can be ignored in most "
            "cases)."
        )
        validate_input_dir(config.execution.exec_env, opts.bids_dir, opts.participant_label)

    # Setup directories
    config.execution.log_dir = output_dir / 'fmriprep' / 'logs'
    # Check and create output and working directories
    config.execution.log_dir.mkdir(exist_ok=True, parents=True)
    output_dir.mkdir(exist_ok=True, parents=True)
    work_dir.mkdir(exist_ok=True, parents=True)

    # First check that bids_dir looks like a BIDS folder
    config.init_layout()
    participant_label = set(config.execution.participant_label)
    missing_subjects = participant_label - set(config.execution.layout.get_subjects())
    if missing_subjects:
        config.loggers.cli.error(
            "One or more participant labels were not found in the BIDS directory: "
            "%s." % ", ".join(missing_subjects))

    config.execution.participant_label = sorted(participant_label)
    config.workflow.skull_strip_template = config.workflow.skull_strip_template[0]

    # Ensure user-defined spatial references for outputs are correctly parsed.
    # Certain options require normalization to a space not explicitly defined by users.
    # These spaces will not be included in the final outputs.
    internal_spaces = []
    if config.workflow.use_aroma:
        # Make sure there's a normalization to FSL for AROMA to use.
        internal_spaces.append('MNI152NLin6Asym:res-2')

    cifti_output = config.workflow.cifti_output
    if cifti_output:
        # CIFTI grayordinates to corresponding FSL-MNI resolutions.
        vol_res = '2' if cifti_output == '91k' else '1'
        internal_spaces += ['fsaverage:den-164k', 'MNI152NLin6Asym:res-%s' % vol_res]

    # These arguments implicitly signal expected output
    spaces = config.execution.output_spaces
    # Add the default standard space if not already present (required by several sub-workflows)
    if "MNI152NLin2009cAsym" not in spaces.get_spaces(nonstandard=False, dim=(3,)):
        internal_spaces.append("MNI152NLin2009cAsym")

    config.workflow.internal_spaces = ' '.join(internal_spaces) or None
