#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Anatomical reference preprocessing workflows
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: init_anat_preproc_wf
.. autofunction:: init_skullstrip_ants_wf

Surface preprocessing
+++++++++++++++++++++

``fmriprep`` uses FreeSurfer_ to reconstruct surfaces from T1w/T2w
structural images.

.. autofunction:: init_surface_recon_wf
.. autofunction:: init_autorecon_resume_wf
.. autofunction:: init_gifti_surface_wf

"""

# Originally coded by Craig Moodie. Refactored by the CRN Developers.


import os.path as op

from niworkflows.nipype.interfaces import ants
from niworkflows.nipype.interfaces import freesurfer as fs
from niworkflows.nipype.interfaces import utility as niu
from niworkflows.nipype.interfaces import io as nio
from niworkflows.nipype.pipeline import engine as pe

from niworkflows.interfaces.registration import RobustMNINormalizationRPT
from niworkflows.anat.skullstrip import afni_wf as init_skullstrip_afni_wf
import niworkflows.data as nid
from niworkflows.interfaces.masks import BrainExtractionRPT
from niworkflows.interfaces.segmentation import FASTRPT, ReconAllRPT

from ..interfaces import (
    DerivativesDataSink, StructuralReference, MakeMidthickness, FSInjectBrainExtracted,
    FSDetectInputs, NormalizeSurf, GiftiNameSource, TemplateDimensions, Conform, Reorient
)
from ..utils.misc import fix_multi_T1w_source_name, add_suffix


#  pylint: disable=R0914
def init_anat_preproc_wf(skull_strip_ants, output_spaces, template, debug, freesurfer,
                         longitudinal, omp_nthreads, hires, reportlets_dir, output_dir,
                         name='anat_preproc_wf'):
    r"""
    This workflow controls the anatomical preprocessing stages of FMRIPREP.

    This includes:

     - Creation of a structural template
     - Skull-stripping and bias correction
     - Tissue segmentation
     - Normalization
     - Surface reconstruction with FreeSurfer

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.anatomical import init_anat_preproc_wf
        wf = init_anat_preproc_wf(omp_nthreads=1,
                                  reportlets_dir='.',
                                  output_dir='.',
                                  template='MNI152NLin2009cAsym',
                                  output_spaces=['T1w', 'fsnative',
                                                 'template', 'fsaverage5'],
                                  skull_strip_ants=True,
                                  freesurfer=True,
                                  longitudinal=False,
                                  debug=False,
                                  hires=True)

    **Parameters**

        skull_strip_ants : bool
            Use ANTs BrainExtraction.sh-based skull-stripping workflow.
            If ``False``, uses a faster AFNI-based workflow
        output_spaces : list
            List of output spaces functional images are to be resampled to.

            Some pipeline components will only be instantiated for some output spaces.

            Valid spaces:

              - T1w
              - template
              - fsnative
              - fsaverage (or other pre-existing FreeSurfer templates)
        template : str
            Name of template targeted by `'template'` output space
        debug : bool
            Enable debugging outputs
        freesurfer : bool
            Enable FreeSurfer surface reconstruction (may increase runtime)
        longitudinal : bool
            Create unbiased structural template, regardless of number of inputs
            (may increase runtime)
        omp_nthreads : int
            Maximum number of threads an individual process may use
        hires : bool
            Enable sub-millimeter preprocessing in FreeSurfer
        reportlets_dir : str
            Directory in which to save reportlets
        output_dir : str
            Directory in which to save derivatives
        name : str, optional
            Workflow name (default: anat_preproc_wf)


    **Inputs**

        t1w
            List of T1-weighted structural images
        t2w
            List of T2-weighted structural images
        subjects_dir
            FreeSurfer SUBJECTS_DIR


    **Outputs**

        t1_preproc
            Bias-corrected structural template, defining T1w space
        t1_brain
            Skull-stripped ``t1_preproc``
        t1_mask
            Mask of the skull-stripped template image
        t1_seg
            Segmentation of preprocessed structural image, including
            gray-matter (GM), white-matter (WM) and cerebrospinal fluid (CSF)
        t1_tpms
            List of tissue probability maps in T1w space
        t1_2_mni
            T1w template, normalized to MNI space
        t1_2_mni_forward_transform
            ANTs-compatible affine-and-warp transform file
        t1_2_mni_reverse_transform
            ANTs-compatible affine-and-warp transform file (inverse)
        mni_mask
            Mask of skull-stripped template, in MNI space
        mni_seg
            Segmentation, resampled into MNI space
        mni_tpms
            List of tissue probability maps in MNI space
        subjects_dir
            FreeSurfer SUBJECTS_DIR
        subject_id
            FreeSurfer subject ID
        fs_2_t1_transform
            Affine transform from FreeSurfer subject space to T1w space
        surfaces
            GIFTI surfaces (gray/white boundary, midthickness, pial, inflated)

    **Subworkflows**

        * :py:func:`~fmriprep.workflows.anatomical.init_skullstrip_ants_wf`
        * :py:func:`~fmriprep.workflows.anatomical.init_surface_recon_wf`

    """

    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(fields=['t1w', 't2w', 'subjects_dir', 'subject_id']),
        name='inputnode')
    outputnode = pe.Node(niu.IdentityInterface(
        fields=['t1_preproc', 't1_brain', 't1_mask', 't1_seg', 't1_tpms',
                't1_2_mni', 't1_2_mni_forward_transform', 't1_2_mni_reverse_transform',
                'mni_mask', 'mni_seg', 'mni_tpms',
                'subjects_dir', 'subject_id', 'fs_2_t1_transform', 'surfaces']),
        name='outputnode')

    # 0. Reorient T1w image(s) to RAS and resample to common voxel space
    t1_template_dimensions = pe.Node(TemplateDimensions(), name='t1_template_dimensions')
    t1_conform = pe.MapNode(Conform(), iterfield='in_file', name='t1_conform')

    # 1. Align and merge if several T1w images are provided
    t1_merge = pe.Node(
        # StructuralReference is fs.RobustTemplate if > 1 volume, copying otherwise
        StructuralReference(auto_detect_sensitivity=True,
                            initial_timepoint=1,      # For deterministic behavior
                            intensity_scaling=True,   # 7-DOF (rigid + intensity)
                            subsample_threshold=200,
                            ),
        name='t1_merge')

    # 1.5 Reorient template to RAS, if needed (mri_robust_template sets LIA)
    t1_reorient = pe.Node(Reorient(), name='t1_reorient')

    # 2. T1 Bias Field Correction
    # Bias field correction is handled in skull strip workflows.

    # 3. Skull-stripping
    skullstrip_wf = init_skullstrip_afni_wf(name='skullstrip_afni_wf')
    if skull_strip_ants:
        skullstrip_wf = init_skullstrip_ants_wf(name='skullstrip_ants_wf',
                                                debug=debug,
                                                omp_nthreads=omp_nthreads)

    # 4. Segmentation
    t1_seg = pe.Node(FASTRPT(generate_report=True, segments=True,
                             no_bias=True, probability_maps=True),
                     name='t1_seg', mem_gb=3)

    # 5. Spatial normalization (T1w to MNI registration)
    t1_2_mni = pe.Node(
        RobustMNINormalizationRPT(
            float=True,
            generate_report=True,
            num_threads=omp_nthreads,
            flavor='testing' if debug else 'precise',
        ),
        name='t1_2_mni',
        n_procs=omp_nthreads
    )

    # Resample the brain mask and the tissue probability maps into mni space
    mni_mask = pe.Node(
        ants.ApplyTransforms(dimension=3, default_value=0, float=True,
                             interpolation='NearestNeighbor'),
        name='mni_mask'
    )

    mni_seg = pe.Node(
        ants.ApplyTransforms(dimension=3, default_value=0, float=True,
                             interpolation='NearestNeighbor'),
        name='mni_seg'
    )

    mni_tpms = pe.MapNode(
        ants.ApplyTransforms(dimension=3, default_value=0, float=True,
                             interpolation='Linear'),
        iterfield=['input_image'],
        name='mni_tpms'
    )

    def set_threads(in_list, maximum):
        return min(len(in_list), maximum)

    def len_above_thresh(in_list, threshold, longitudinal):
        if longitudinal:
            return False
        return len(in_list) > threshold

    workflow.connect([
        (inputnode, t1_template_dimensions, [('t1w', 't1w_list')]),
        (t1_template_dimensions, t1_conform, [
            ('t1w_valid_list', 'in_file'),
            ('target_zooms', 'target_zooms'),
            ('target_shape', 'target_shape')]),
        (t1_conform, t1_merge, [
            ('out_file', 'in_files'),
            (('out_file', set_threads, omp_nthreads), 'num_threads'),
            (('out_file', len_above_thresh, 2, longitudinal), 'fixed_timepoint'),
            (('out_file', len_above_thresh, 2, longitudinal), 'no_iteration'),
            (('out_file', add_suffix, '_template'), 'out_file')]),
        (t1_merge, t1_reorient, [('out_file', 'in_file')]),
        (t1_reorient, skullstrip_wf, [('out_file', 'inputnode.in_file')]),
        (skullstrip_wf, t1_seg, [('outputnode.out_file', 'in_files')]),
        (skullstrip_wf, outputnode, [('outputnode.bias_corrected', 't1_preproc'),
                                     ('outputnode.out_file', 't1_brain'),
                                     ('outputnode.out_mask', 't1_mask')]),
        (t1_seg, outputnode, [('tissue_class_map', 't1_seg'),
                              ('probability_maps', 't1_tpms')]),
    ])

    if 'template' in output_spaces:
        template_str = nid.TEMPLATE_MAP[template]
        ref_img = op.join(nid.get_dataset(template_str), '1mm_T1.nii.gz')

        t1_2_mni.inputs.template = template_str
        mni_mask.inputs.reference_image = ref_img
        mni_seg.inputs.reference_image = ref_img
        mni_tpms.inputs.reference_image = ref_img

        workflow.connect([
            (skullstrip_wf, t1_2_mni, [('outputnode.bias_corrected', 'moving_image')]),
            (skullstrip_wf, t1_2_mni, [('outputnode.out_mask', 'moving_mask')]),
            (skullstrip_wf, mni_mask, [('outputnode.out_mask', 'input_image')]),
            (t1_2_mni, mni_mask, [('composite_transform', 'transforms')]),
            (t1_seg, mni_seg, [('tissue_class_map', 'input_image')]),
            (t1_2_mni, mni_seg, [('composite_transform', 'transforms')]),
            (t1_seg, mni_tpms, [('probability_maps', 'input_image')]),
            (t1_2_mni, mni_tpms, [('composite_transform', 'transforms')]),
            (t1_2_mni, outputnode, [
                ('warped_image', 't1_2_mni'),
                ('composite_transform', 't1_2_mni_forward_transform'),
                ('inverse_composite_transform', 't1_2_mni_reverse_transform')]),
            (mni_mask, outputnode, [('output_image', 'mni_mask')]),
            (mni_seg, outputnode, [('output_image', 'mni_seg')]),
            (mni_tpms, outputnode, [('output_image', 'mni_tpms')]),
        ])

    # 6. FreeSurfer reconstruction
    if freesurfer:
        surface_recon_wf = init_surface_recon_wf(name='surface_recon_wf',
                                                 omp_nthreads=omp_nthreads, hires=hires)

        workflow.connect([
            (inputnode, surface_recon_wf, [
                ('t2w', 'inputnode.t2w'),
                ('subjects_dir', 'inputnode.subjects_dir'),
                ('subject_id', 'inputnode.subject_id')]),
            (t1_reorient, surface_recon_wf, [('out_file', 'inputnode.t1w')]),
            (skullstrip_wf, surface_recon_wf, [
                ('outputnode.out_file', 'inputnode.skullstripped_t1')]),
            (surface_recon_wf, outputnode, [
                ('outputnode.subjects_dir', 'subjects_dir'),
                ('outputnode.subject_id', 'subject_id'),
                ('outputnode.fs_2_t1_transform', 'fs_2_t1_transform'),
                ('outputnode.surfaces', 'surfaces')]),
        ])

    anat_reports_wf = init_anat_reports_wf(
        reportlets_dir=reportlets_dir, skull_strip_ants=skull_strip_ants,
        output_spaces=output_spaces, template=template, freesurfer=freesurfer)
    workflow.connect([
        (inputnode, anat_reports_wf, [
            (('t1w', fix_multi_T1w_source_name), 'inputnode.source_file')]),
        (t1_template_dimensions, anat_reports_wf, [
            ('out_report', 'inputnode.t1_conform_report')]),
        (t1_seg, anat_reports_wf, [('out_report', 'inputnode.t1_seg_report')]),
    ])

    if skull_strip_ants:
        workflow.connect([
            (skullstrip_wf, anat_reports_wf, [
                ('outputnode.out_report', 'inputnode.t1_skull_strip_report')])
        ])
    if freesurfer:
        workflow.connect([
            (surface_recon_wf, anat_reports_wf, [
                ('outputnode.out_report', 'inputnode.recon_report')])
        ])
    if 'template' in output_spaces:
        workflow.connect([
            (t1_2_mni, anat_reports_wf, [('out_report', 'inputnode.t1_2_mni_report')]),
        ])

    anat_derivatives_wf = init_anat_derivatives_wf(output_dir=output_dir,
                                                   output_spaces=output_spaces,
                                                   template=template,
                                                   freesurfer=freesurfer)

    workflow.connect([
        (inputnode, anat_derivatives_wf, [
            (('t1w', fix_multi_T1w_source_name), 'inputnode.source_file')]),
        (outputnode, anat_derivatives_wf, [
            ('t1_preproc', 'inputnode.t1_preproc'),
            ('t1_mask', 'inputnode.t1_mask'),
            ('t1_seg', 'inputnode.t1_seg'),
            ('t1_tpms', 'inputnode.t1_tpms'),
            ('t1_2_mni_forward_transform', 'inputnode.t1_2_mni_forward_transform'),
            ('t1_2_mni', 'inputnode.t1_2_mni'),
            ('mni_mask', 'inputnode.mni_mask'),
            ('mni_seg', 'inputnode.mni_seg'),
            ('mni_tpms', 'inputnode.mni_tpms'),
            ('surfaces', 'inputnode.surfaces'),
        ]),
    ])

    return workflow


def init_skullstrip_ants_wf(debug, omp_nthreads, name='skullstrip_ants_wf'):
    r"""
    This workflow performs skull-stripping using ANTs' ``BrainExtraction.sh``

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.anatomical import init_skullstrip_ants_wf
        wf = init_skullstrip_ants_wf(debug=False, omp_nthreads=1)

    **Parameters**

        debug : bool
            Enable debugging outputs
        omp_nthreads : int
            Maximum number of threads an individual process may use

    **Inputs**

        in_file
            T1-weighted structural image to skull-strip

    **Outputs**

        bias_corrected
            Bias-corrected ``in_file``, before skull-stripping
        out_file
            Skull-stripped ``in_file``
        out_mask
            Binary mask of the skull-stripped ``in_file``
        out_report
            Reportlet visualizing quality of skull-stripping

    """
    from niworkflows.data import get_ants_oasis_template_ras

    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(niu.IdentityInterface(fields=['in_file']),
                        name='inputnode')
    outputnode = pe.Node(niu.IdentityInterface(
        fields=['bias_corrected', 'out_file', 'out_mask', 'out_report']), name='outputnode')

    t1_skull_strip = pe.Node(BrainExtractionRPT(
        dimension=3, use_floatingpoint_precision=1,
        debug=debug, generate_report=True,
        num_threads=omp_nthreads, keep_temporary_files=1),
        name='t1_skull_strip', n_procs=omp_nthreads)

    t1_skull_strip.inputs.brain_template = op.join(
        get_ants_oasis_template_ras(),
        'T_template0.nii.gz'
    )
    t1_skull_strip.inputs.brain_probability_mask = op.join(
        get_ants_oasis_template_ras(),
        'T_template0_BrainCerebellumProbabilityMask.nii.gz'
    )
    t1_skull_strip.inputs.extraction_registration_mask = op.join(
        get_ants_oasis_template_ras(),
        'T_template0_BrainCerebellumRegistrationMask.nii.gz'
    )

    workflow.connect([
        (inputnode, t1_skull_strip, [('in_file', 'anatomical_image')]),
        (t1_skull_strip, outputnode, [('BrainExtractionMask', 'out_mask'),
                                      ('BrainExtractionBrain', 'out_file'),
                                      ('N4Corrected0', 'bias_corrected'),
                                      ('out_report', 'out_report')])
    ])

    return workflow


def init_surface_recon_wf(omp_nthreads, hires, name='surface_recon_wf'):
    r"""
    This workflow reconstructs anatomical surfaces using FreeSurfer's ``recon-all``.

    Reconstruction is performed in three phases.
    The first phase initializes the subject with T1w and T2w (if available)
    structural images and performs basic reconstruction (``autorecon1``) with the
    exception of skull-stripping.
    For example, a subject with only one session with T1w and T2w images
    would be processed by the following command::

        $ recon-all -sd <output dir>/freesurfer -subjid sub-<subject_label> \
            -i <bids-root>/sub-<subject_label>/anat/sub-<subject_label>_T1w.nii.gz \
            -T2 <bids-root>/sub-<subject_label>/anat/sub-<subject_label>_T2w.nii.gz \
            -autorecon1 \
            -noskullstrip

    The second phase imports an externally computed skull-stripping mask.
    The final phase resumes reconstruction, using the T2w image to assist
    in finding the pial surface, if available.
    See :py:func:`~fmriprep.workflows.anatomical.init_autorecon_resume_wf` for details.


    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.anatomical import init_surface_recon_wf
        wf = init_surface_recon_wf(omp_nthreads=1, hires=True)

    **Parameters**

        omp_nthreads : int
            Maximum number of threads an individual process may use
        hires : bool
            Enable sub-millimeter preprocessing in FreeSurfer

    **Inputs**

        t1w
            List of T1-weighted structural images
        t2w
            List of T2-weighted structural images (only first used)
        skullstripped_t1
            Skull-stripped T1-weighted image (or mask of image)
        subjects_dir
            FreeSurfer SUBJECTS_DIR
        subject_id
            FreeSurfer subject ID

    **Outputs**

        subjects_dir
            FreeSurfer SUBJECTS_DIR
        subject_id
            FreeSurfer subject ID
        fs_2_t1_transform
            FSL-style affine matrix translating from FreeSurfer T1.mgz to T1w
        surfaces
            GIFTI surfaces for gray/white matter boundary, pial surface,
            midthickness (or graymid) surface, and inflated surfaces
        out_report
            Reportlet visualizing quality of surface alignment

    **Subworkflows**

        * :py:func:`~fmriprep.workflows.anatomical.init_autorecon_resume_wf`
        * :py:func:`~fmriprep.workflows.anatomical.init_gifti_surface_wf`
    """

    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=['t1w', 't2w', 'skullstripped_t1', 'subjects_dir', 'subject_id']),
        name='inputnode')
    outputnode = pe.Node(
        niu.IdentityInterface(
            fields=['subjects_dir', 'subject_id', 'fs_2_t1_transform', 'surfaces', 'out_report']),
        name='outputnode')

    recon_config = pe.Node(FSDetectInputs(hires_enabled=hires), name='recon_config',
                           run_without_submitting=True)

    autorecon1 = pe.Node(
        fs.ReconAll(
            directive='autorecon1',
            flags='-noskullstrip',
            openmp=omp_nthreads),
        name='autorecon1',
        n_procs=omp_nthreads)
    autorecon1.interface._can_resume = False

    skull_strip_extern = pe.Node(FSInjectBrainExtracted(), name='skull_strip_extern',
                                 run_without_submitting=True)

    fs_transform = pe.Node(
        fs.Tkregister2(fsl_out='freesurfer2subT1.mat', reg_header=True),
        name='fs_transform')

    autorecon_resume_wf = init_autorecon_resume_wf(omp_nthreads=omp_nthreads)
    gifti_surface_wf = init_gifti_surface_wf()

    workflow.connect([
        # Configuration
        (inputnode, recon_config, [('t1w', 't1w_list'),
                                   ('t2w', 't2w_list')]),
        # Passing subjects_dir / subject_id enforces serial order
        (inputnode, autorecon1, [('subjects_dir', 'subjects_dir'),
                                 ('subject_id', 'subject_id')]),
        (autorecon1, skull_strip_extern, [('subjects_dir', 'subjects_dir'),
                                          ('subject_id', 'subject_id')]),
        (skull_strip_extern, autorecon_resume_wf, [('subjects_dir', 'inputnode.subjects_dir'),
                                                   ('subject_id', 'inputnode.subject_id')]),
        (autorecon_resume_wf, gifti_surface_wf, [
            ('outputnode.subjects_dir', 'inputnode.subjects_dir'),
            ('outputnode.subject_id', 'inputnode.subject_id')]),
        # Reconstruction phases
        (inputnode, autorecon1, [('t1w', 'T1_files')]),
        (recon_config, autorecon1, [('t2w', 'T2_file'),
                                    ('hires', 'hires'),
                                    # First run only (recon-all saves expert options)
                                    ('mris_inflate', 'mris_inflate')]),
        (inputnode, skull_strip_extern, [('skullstripped_t1', 'in_brain')]),
        (recon_config, autorecon_resume_wf, [('use_t2w', 'inputnode.use_T2')]),
        # Construct transform from FreeSurfer conformed image to FMRIPREP
        # reoriented image
        (inputnode, fs_transform, [('t1w', 'target_image')]),
        (autorecon1, fs_transform, [('T1', 'moving_image')]),
        # Output
        (autorecon_resume_wf, outputnode, [('outputnode.subjects_dir', 'subjects_dir'),
                                           ('outputnode.subject_id', 'subject_id'),
                                           ('outputnode.out_report', 'out_report')]),
        (gifti_surface_wf, outputnode, [('outputnode.surfaces', 'surfaces')]),
        (fs_transform, outputnode, [('fsl_file', 'fs_2_t1_transform')]),
    ])

    return workflow


def init_autorecon_resume_wf(omp_nthreads, name='autorecon_resume_wf'):
    r"""
    This workflow resumes recon-all execution, assuming the `-autorecon1` stage
    has been completed.

    In order to utilize resources efficiently, this is broken down into five
    sub-stages; after the first stage, the second and third stages may be run
    simultaneously, and the fourth and fifth stages may be run simultaneously,
    if resources permit::

        $ recon-all -sd <output dir>/freesurfer -subjid sub-<subject_label> \
            -autorecon2-volonly
        $ recon-all -sd <output dir>/freesurfer -subjid sub-<subject_label> \
            -autorecon-hemi lh \
            -noparcstats -nocortparc2 -noparcstats2 -nocortparc3 \
            -noparcstats3 -nopctsurfcon -nohyporelabel -noaparc2aseg \
            -noapas2aseg -nosegstats -nowmparc -nobalabels
        $ recon-all -sd <output dir>/freesurfer -subjid sub-<subject_label> \
            -autorecon-hemi rh \
            -noparcstats -nocortparc2 -noparcstats2 -nocortparc3 \
            -noparcstats3 -nopctsurfcon -nohyporelabel -noaparc2aseg \
            -noapas2aseg -nosegstats -nowmparc -nobalabels
        $ recon-all -sd <output dir>/freesurfer -subjid sub-<subject_label> \
            -autorecon3 -hemi lh -T2pial
        $ recon-all -sd <output dir>/freesurfer -subjid sub-<subject_label> \
            -autorecon3 -hemi rh -T2pial

    The excluded steps in the second and third stages (``-no<option>``) are not
    fully hemisphere independent, and are therefore postponed to the final two
    stages.

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.anatomical import init_autorecon_resume_wf
        wf = init_autorecon_resume_wf(omp_nthreads=1)

    **Inputs**

        subjects_dir
            FreeSurfer SUBJECTS_DIR
        subject_id
            FreeSurfer subject ID
        use_T2
            Refine pial surface using T2w images

    **Outputs**

        subjects_dir
            FreeSurfer SUBJECTS_DIR
        subject_id
            FreeSurfer subject ID
        out_report
            Reportlet visualizing quality of surface alignment

    """
    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=['subjects_dir', 'subject_id', 'use_T2']),
        name='inputnode')

    outputnode = pe.Node(
        niu.IdentityInterface(
            fields=['subjects_dir', 'subject_id', 'out_report']),
        name='outputnode')

    autorecon2_vol = pe.Node(
        fs.ReconAll(directive='autorecon2-volonly', openmp=omp_nthreads),
        n_procs=omp_nthreads,
        name='autorecon2_vol')

    autorecon_surfs = pe.MapNode(
        fs.ReconAll(
            directive='autorecon-hemi',
            flags=['-noparcstats', '-nocortparc2', '-noparcstats2',
                   '-nocortparc3', '-noparcstats3', '-nopctsurfcon',
                   '-nohyporelabel', '-noaparc2aseg', '-noapas2aseg',
                   '-nosegstats', '-nowmparc', '-nobalabels'],
            openmp=omp_nthreads),
        iterfield='hemi', n_procs=omp_nthreads,
        name='autorecon_surfs')
    autorecon_surfs.inputs.hemi = ['lh', 'rh']

    autorecon3 = pe.MapNode(
        fs.ReconAll(directive='autorecon3', openmp=omp_nthreads),
        iterfield='hemi', n_procs=omp_nthreads,
        name='autorecon3')
    autorecon3.inputs.hemi = ['lh', 'rh']

    # Only generate the report once; should be nothing to do
    recon_report = pe.Node(
        ReconAllRPT(directive='autorecon3', generate_report=True),
        name='recon_report')

    def _dedup(in_list):
        vals = set(in_list)
        if len(vals) > 1:
            raise ValueError(
                "Non-identical values can't be deduplicated:\n{!r}".format(in_list))
        return vals.pop()

    workflow.connect([
        (inputnode, autorecon3, [('use_T2', 'use_T2')]),
        (inputnode, autorecon2_vol, [('subjects_dir', 'subjects_dir'),
                                     ('subject_id', 'subject_id')]),
        (autorecon2_vol, autorecon_surfs, [('subjects_dir', 'subjects_dir'),
                                           ('subject_id', 'subject_id')]),
        (autorecon_surfs, autorecon3, [(('subjects_dir', _dedup), 'subjects_dir'),
                                       (('subject_id', _dedup), 'subject_id')]),
        (autorecon3, outputnode, [(('subjects_dir', _dedup), 'subjects_dir'),
                                  (('subject_id', _dedup), 'subject_id')]),
        (autorecon3, recon_report, [(('subjects_dir', _dedup), 'subjects_dir'),
                                    (('subject_id', _dedup), 'subject_id')]),
        (recon_report, outputnode, [('out_report', 'out_report')]),
    ])

    return workflow


def init_gifti_surface_wf(name='gifti_surface_wf'):
    r"""
    This workflow prepares GIFTI surfaces from a FreeSurfer subjects directory

    If midthickness (or graymid) surfaces do not exist, they are generated and
    saved to the subject directory as ``lh/rh.midthickness``.
    These, along with the gray/white matter boundary (``lh/rh.smoothwm``), pial
    sufaces (``lh/rh.pial``) and inflated surfaces (``lh/rh.inflated``) are
    converted to GIFTI files.
    Additionally, the vertex coordinates are :py:class:`recentered
    <fmriprep.interfaces.NormalizeSurf>` to align with native T1w space.

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.anatomical import init_gifti_surface_wf
        wf = init_gifti_surface_wf()

    **Inputs**

        subjects_dir
            FreeSurfer SUBJECTS_DIR
        subject_id
            FreeSurfer subject ID

    **Outputs**

        surfaces
            GIFTI surfaces for gray/white matter boundary, pial surface,
            midthickness (or graymid) surface, and inflated surfaces

    """
    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(niu.IdentityInterface(['subjects_dir', 'subject_id']), name='inputnode')
    outputnode = pe.Node(niu.IdentityInterface(['surfaces']), name='outputnode')

    get_surfaces = pe.Node(nio.FreeSurferSource(), name='get_surfaces')

    midthickness = pe.MapNode(
        MakeMidthickness(thickness=True, distance=0.5, out_name='midthickness'),
        iterfield='in_file',
        name='midthickness')

    save_midthickness = pe.Node(nio.DataSink(parameterization=False),
                                name='save_midthickness')

    surface_list = pe.Node(niu.Merge(4, ravel_inputs=True),
                           name='surface_list', run_without_submitting=True)
    fs_2_gii = pe.MapNode(fs.MRIsConvert(out_datatype='gii'),
                          iterfield='in_file', name='fs_2_gii')
    fix_surfs = pe.MapNode(NormalizeSurf(), iterfield='in_file', name='fix_surfs')

    workflow.connect([
        (inputnode, get_surfaces, [('subjects_dir', 'subjects_dir'),
                                   ('subject_id', 'subject_id')]),
        (inputnode, save_midthickness, [('subjects_dir', 'base_directory'),
                                        ('subject_id', 'container')]),
        # Generate midthickness surfaces and save to FreeSurfer derivatives
        (get_surfaces, midthickness, [('smoothwm', 'in_file'),
                                      ('graymid', 'graymid')]),
        (midthickness, save_midthickness, [('out_file', 'surf.@graymid')]),
        # Produce valid GIFTI surface files (dense mesh)
        (get_surfaces, surface_list, [('smoothwm', 'in1'),
                                      ('pial', 'in2'),
                                      ('inflated', 'in3')]),
        (save_midthickness, surface_list, [('out_file', 'in4')]),
        (surface_list, fs_2_gii, [('out', 'in_file')]),
        (fs_2_gii, fix_surfs, [('converted', 'in_file')]),
        (fix_surfs, outputnode, [('out_file', 'surfaces')]),
    ])
    return workflow


def init_anat_reports_wf(reportlets_dir, skull_strip_ants, output_spaces,
                         template, freesurfer, name='anat_reports_wf'):
    """
    Set up a battery of datasinks to store reports in the right location
    """
    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=['source_file', 't1_conform_report', 't1_seg_report',
                    't1_2_mni_report', 't1_skull_strip_report', 'recon_report']),
        name='inputnode')

    ds_t1_conform_report = pe.Node(
        DerivativesDataSink(base_directory=reportlets_dir, suffix='conform'),
        name='ds_t1_conform_report', run_without_submitting=True)

    ds_t1_seg_report = pe.Node(
        DerivativesDataSink(base_directory=reportlets_dir, suffix='t1_seg'),
        name='ds_t1_seg_report', run_without_submitting=True)

    ds_t1_2_mni_report = pe.Node(
        DerivativesDataSink(base_directory=reportlets_dir, suffix='t1_2_mni'),
        name='ds_t1_2_mni_report', run_without_submitting=True)

    ds_t1_skull_strip_report = pe.Node(
        DerivativesDataSink(base_directory=reportlets_dir, suffix='t1_skull_strip'),
        name='ds_t1_skull_strip_report', run_without_submitting=True)

    ds_recon_report = pe.Node(
        DerivativesDataSink(base_directory=reportlets_dir, suffix='reconall'),
        name='ds_recon_report', run_without_submitting=True)

    workflow.connect([
        (inputnode, ds_t1_conform_report, [('source_file', 'source_file'),
                                           ('t1_conform_report', 'in_file')]),
        (inputnode, ds_t1_seg_report, [('source_file', 'source_file'),
                                       ('t1_seg_report', 'in_file')]),
    ])

    if skull_strip_ants:
        workflow.connect([
            (inputnode, ds_t1_skull_strip_report, [('source_file', 'source_file'),
                                                   ('t1_skull_strip_report', 'in_file')])
        ])
    if freesurfer:
        workflow.connect([
            (inputnode, ds_recon_report, [('source_file', 'source_file'),
                                          ('recon_report', 'in_file')])
        ])
    if 'template' in output_spaces:
        workflow.connect([
            (inputnode, ds_t1_2_mni_report, [('source_file', 'source_file'),
                                             ('t1_2_mni_report', 'in_file')])
        ])

    return workflow


def init_anat_derivatives_wf(output_dir, output_spaces, template, freesurfer,
                             name='anat_derivatives_wf'):
    """
    Set up a battery of datasinks to store derivatives in the right location
    """
    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=['source_file', 't1_preproc', 't1_mask', 't1_seg', 't1_tpms',
                    't1_2_mni_forward_transform', 't1_2_mni', 'mni_mask',
                    'mni_seg', 'mni_tpms', 'surfaces']),
        name='inputnode')

    ds_t1_preproc = pe.Node(
        DerivativesDataSink(base_directory=output_dir, suffix='preproc'),
        name='ds_t1_preproc', run_without_submitting=True)

    ds_t1_mask = pe.Node(
        DerivativesDataSink(base_directory=output_dir, suffix='brainmask'),
        name='ds_t1_mask', run_without_submitting=True)

    ds_t1_seg = pe.Node(
        DerivativesDataSink(base_directory=output_dir, suffix='dtissue'),
        name='ds_t1_seg', run_without_submitting=True)

    ds_t1_tpms = pe.Node(
        DerivativesDataSink(base_directory=output_dir,
                            suffix='class-{extra_value}_probtissue'),
        name='ds_t1_tpms', run_without_submitting=True)
    ds_t1_tpms.inputs.extra_values = ['CSF', 'GM', 'WM']

    suffix_fmt = 'space-{}_{}'.format
    ds_t1_mni = pe.Node(
        DerivativesDataSink(base_directory=output_dir,
                            suffix=suffix_fmt(template, 'preproc')),
        name='ds_t1_mni', run_without_submitting=True)

    ds_mni_mask = pe.Node(
        DerivativesDataSink(base_directory=output_dir,
                            suffix=suffix_fmt(template, 'brainmask')),
        name='ds_mni_mask', run_without_submitting=True)

    ds_mni_seg = pe.Node(
        DerivativesDataSink(base_directory=output_dir,
                            suffix=suffix_fmt(template, 'dtissue')),
        name='ds_mni_seg', run_without_submitting=True)

    ds_mni_tpms = pe.Node(
        DerivativesDataSink(base_directory=output_dir,
                            suffix=suffix_fmt(template, 'class-{extra_value}_probtissue')),
        name='ds_mni_tpms', run_without_submitting=True)
    ds_mni_tpms.inputs.extra_values = ['CSF', 'GM', 'WM']

    ds_t1_mni_warp = pe.Node(
        DerivativesDataSink(base_directory=output_dir, suffix=suffix_fmt(template, 'warp')),
        name='ds_t1_mni_warp', run_without_submitting=True)

    name_surfs = pe.MapNode(GiftiNameSource(pattern=r'(?P<LR>[lr])h.(?P<surf>.+)_converted.gii',
                                            template='{surf}.{LR}.surf'),
                            iterfield='in_file',
                            name='name_surfs',
                            run_without_submitting=True)

    ds_surfs = pe.MapNode(
        DerivativesDataSink(base_directory=output_dir),
        iterfield=['in_file', 'suffix'], name='ds_surfs', run_without_submitting=True)

    workflow.connect([
        (inputnode, ds_t1_preproc, [('source_file', 'source_file'),
                                    ('t1_preproc', 'in_file')]),
        (inputnode, ds_t1_mask, [('source_file', 'source_file'),
                                 ('t1_mask', 'in_file')]),
        (inputnode, ds_t1_seg, [('source_file', 'source_file'),
                                ('t1_seg', 'in_file')]),
        (inputnode, ds_t1_tpms, [('source_file', 'source_file'),
                                 ('t1_tpms', 'in_file')]),
    ])

    if freesurfer:
        workflow.connect([
            (inputnode, name_surfs, [('surfaces', 'in_file')]),
            (inputnode, ds_surfs, [('source_file', 'source_file'),
                                   ('surfaces', 'in_file')]),
            (name_surfs, ds_surfs, [('out_name', 'suffix')]),
        ])
    if 'template' in output_spaces:
        workflow.connect([
            (inputnode, ds_t1_mni_warp, [('source_file', 'source_file'),
                                         ('t1_2_mni_forward_transform', 'in_file')]),
            (inputnode, ds_t1_mni, [('source_file', 'source_file'),
                                    ('t1_2_mni', 'in_file')]),
            (inputnode, ds_mni_mask, [('source_file', 'source_file'),
                                      ('mni_mask', 'in_file')]),
            (inputnode, ds_mni_seg, [('source_file', 'source_file'),
                                     ('mni_seg', 'in_file')]),
            (inputnode, ds_mni_tpms, [('source_file', 'source_file'),
                                      ('mni_tpms', 'in_file')]),
        ])

    return workflow
