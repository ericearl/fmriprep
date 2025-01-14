#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
fMRIprep base processing workflows
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: init_fmriprep_wf
.. autofunction:: init_single_subject_wf

"""

import sys
import os
from copy import deepcopy

from niworkflows.nipype.pipeline import engine as pe
from niworkflows.nipype.interfaces import utility as niu

from ..interfaces import (
    BIDSDataGrabber, BIDSFreeSurferDir, BIDSInfo, SubjectSummary, AboutSummary,
    DerivativesDataSink
)
from ..utils.bids import collect_data
from ..utils.misc import fix_multi_T1w_source_name
from ..info import __version__

from .anatomical import init_anat_preproc_wf
from .bold import init_func_preproc_wf


def init_fmriprep_wf(subject_list, task_id, run_uuid,
                     ignore, debug, low_mem, anat_only, longitudinal, omp_nthreads,
                     skull_strip_ants, work_dir, output_dir, bids_dir,
                     freesurfer, output_spaces, template, hires,
                     bold2t1w_dof, fmap_bspline, fmap_demean, use_syn, force_syn,
                     use_aroma, ignore_aroma_err, output_grid_ref):
    """
    This workflow organizes the execution of FMRIPREP, with a sub-workflow for
    each subject.

    If FreeSurfer is to be used, a FreeSurfer derivatives folder is created and
    populated with any needed template subjects.

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.base import init_fmriprep_wf
        wf = init_fmriprep_wf(subject_list=['fmripreptest'],
                              task_id='',
                              run_uuid='X',
                              ignore=[],
                              debug=False,
                              low_mem=False,
                              anat_only=False,
                              longitudinal=False,
                              omp_nthreads=1,
                              skull_strip_ants=True,
                              work_dir='.',
                              output_dir='.',
                              bids_dir='.',
                              freesurfer=True,
                              output_spaces=['T1w', 'fsnative',
                                            'template', 'fsaverage5'],
                              template='MNI152NLin2009cAsym',
                              hires=True,
                              bold2t1w_dof=9,
                              fmap_bspline=False,
                              fmap_demean=True,
                              use_syn=True,
                              force_syn=True,
                              use_aroma=False,
                              ignore_aroma_err=False,
                              output_grid_ref=None)


    Parameters

        subject_list : list
            List of subject labels
        task_id : str or None
            Task ID of BOLD series to preprocess, or ``None`` to preprocess all
        run_uuid : str
            Unique identifier for execution instance
        ignore : list
            Preprocessing steps to skip (may include "slicetiming", "fieldmaps")
        debug : bool
            Enable debugging outputs
        low_mem : bool
            Write uncompressed .nii files in some cases to reduce memory usage
        anat_only : bool
            Disable functional workflows
        longitudinal : bool
            Treat multiple sessions as longitudinal (may increase runtime)
            See sub-workflows for specific differences
        omp_nthreads : int
            Maximum number of threads an individual process may use
        skull_strip_ants : bool
            Use ANTs BrainExtraction.sh-based skull-stripping workflow
            If ``False``, uses a faster AFNI-based workflow
        work_dir : str
            Directory in which to store workflow execution state and temporary files
        output_dir : str
            Directory in which to save derivatives
        bids_dir : str
            Root directory of BIDS dataset
        freesurfer : bool
            Enable FreeSurfer surface reconstruction (may increase runtime)
        output_spaces : list
            List of output spaces functional images are to be resampled to.
            Some parts of pipeline will only be instantiated for some output spaces.

            Valid spaces:

             - T1w
             - template
             - fsnative
             - fsaverage (or other pre-existing FreeSurfer templates)
        template : str
            Name of template targeted by `'template'` output space
        hires : bool
            Enable sub-millimeter preprocessing in FreeSurfer
        bold2t1w_dof : 6, 9 or 12
            Degrees-of-freedom for BOLD-T1w registration
        fmap_bspline : bool
            **Experimental**: Fit B-Spline field using least-squares
        fmap_demean : bool
            Demean voxel-shift map during unwarp
        use_syn : bool
            **Experimental**: Enable ANTs SyN-based susceptibility distortion correction (SDC).
            If fieldmaps are present and enabled, this is not run, by default.
        force_syn : bool
            **Temporary**: Always run SyN-based SDC
        use_aroma : bool
            Perform ICA-AROMA on MNI-resampled functional series
        ignore_aroma_err : bool
            Do not fail on ICA-AROMA errors
        output_grid_ref : str or None
            Path of custom reference image for normalization

    """
    fmriprep_wf = pe.Workflow(name='fmriprep_wf')
    fmriprep_wf.base_dir = work_dir

    if freesurfer:
        fsdir = pe.Node(
            BIDSFreeSurferDir(
                derivatives=output_dir,
                freesurfer_home=os.getenv('FREESURFER_HOME'),
                spaces=output_spaces),
            name='fsdir', run_without_submitting=True)

    reportlets_dir = os.path.join(work_dir, 'reportlets')
    for subject_id in subject_list:
        single_subject_wf = init_single_subject_wf(subject_id=subject_id,
                                                   task_id=task_id,
                                                   name="single_subject_" + subject_id + "_wf",
                                                   ignore=ignore,
                                                   debug=debug,
                                                   low_mem=low_mem,
                                                   anat_only=anat_only,
                                                   longitudinal=longitudinal,
                                                   omp_nthreads=omp_nthreads,
                                                   skull_strip_ants=skull_strip_ants,
                                                   reportlets_dir=reportlets_dir,
                                                   output_dir=output_dir,
                                                   bids_dir=bids_dir,
                                                   freesurfer=freesurfer,
                                                   output_spaces=output_spaces,
                                                   template=template,
                                                   hires=hires,
                                                   bold2t1w_dof=bold2t1w_dof,
                                                   fmap_bspline=fmap_bspline,
                                                   fmap_demean=fmap_demean,
                                                   use_syn=use_syn,
                                                   force_syn=force_syn,
                                                   output_grid_ref=output_grid_ref,
                                                   use_aroma=use_aroma,
                                                   ignore_aroma_err=ignore_aroma_err)

        single_subject_wf.config['execution']['crashdump_dir'] = (
            os.path.join(output_dir, "fmriprep", "sub-" + subject_id, 'log', run_uuid)
        )
        for node in single_subject_wf._get_all_nodes():
            node.config = deepcopy(single_subject_wf.config)
        if freesurfer:
            fmriprep_wf.connect(fsdir, 'subjects_dir',
                                single_subject_wf, 'inputnode.subjects_dir')
        else:
            fmriprep_wf.add_nodes([single_subject_wf])

    return fmriprep_wf


def init_single_subject_wf(subject_id, task_id, name,
                           ignore, debug, low_mem, anat_only, longitudinal, omp_nthreads,
                           skull_strip_ants, reportlets_dir, output_dir, bids_dir,
                           freesurfer, output_spaces, template, hires,
                           bold2t1w_dof, fmap_bspline, fmap_demean, use_syn, force_syn,
                           output_grid_ref, use_aroma, ignore_aroma_err):
    """
    This workflow organizes the preprocessing pipeline for a single subject.
    It collects and reports information about the subject, and prepares
    sub-workflows to perform anatomical and functional preprocessing.

    Anatomical preprocessing is performed in a single workflow, regardless of
    the number of sessions.
    Functional preprocessing is performed using a separate workflow for each
    individual BOLD series.

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.base import init_single_subject_wf
        wf = init_single_subject_wf(subject_id='test',
                                    name='single_subject_wf',
                                    task_id='',
                                    longitudinal=False,
                                    omp_nthreads=1,
                                    freesurfer=True,
                                    reportlets_dir='.',
                                    output_dir='.',
                                    bids_dir='.',
                                    skull_strip_ants=True,
                                    template='MNI152NLin2009cAsym',
                                    output_spaces=['T1w', 'fsnative',
                                                  'template', 'fsaverage5'],
                                    ignore=[],
                                    debug=False,
                                    low_mem=False,
                                    anat_only=False,
                                    hires=True,
                                    bold2t1w_dof=9,
                                    fmap_bspline=False,
                                    fmap_demean=True,
                                    use_syn=True,
                                    force_syn=True,
                                    output_grid_ref=None,
                                    use_aroma=False,
                                    ignore_aroma_err=False)

    Parameters

        subject_id : str
            List of subject labels
        task_id : str or None
            Task ID of BOLD series to preprocess, or ``None`` to preprocess all
        name : str
            Name of workflow
        ignore : list
            Preprocessing steps to skip (may include "slicetiming", "fieldmaps")
        debug : bool
            Enable debugging outputs
        low_mem : bool
            Write uncompressed .nii files in some cases to reduce memory usage
        anat_only : bool
            Disable functional workflows
        longitudinal : bool
            Treat multiple sessions as longitudinal (may increase runtime)
            See sub-workflows for specific differences
        omp_nthreads : int
            Maximum number of threads an individual process may use
        skull_strip_ants : bool
            Use ANTs BrainExtraction.sh-based skull-stripping workflow
            If ``False``, uses a faster AFNI-based workflow
        reportlets_dir : str
            Directory in which to save reportlets
        output_dir : str
            Directory in which to save derivatives
        bids_dir : str
            Root directory of BIDS dataset
        freesurfer : bool
            Enable FreeSurfer surface reconstruction (may increase runtime)
        output_spaces : list
            List of output spaces functional images are to be resampled to.
            Some parts of pipeline will only be instantiated for some output spaces.

            Valid spaces:

             - T1w
             - template
             - fsnative
             - fsaverage (or other pre-existing FreeSurfer templates)
        template : str
            Name of template targeted by `'template'` output space
        hires : bool
            Enable sub-millimeter preprocessing in FreeSurfer
        bold2t1w_dof : 6, 9 or 12
            Degrees-of-freedom for BOLD-T1w registration
        fmap_bspline : bool
            **Experimental**: Fit B-Spline field using least-squares
        fmap_demean : bool
            Demean voxel-shift map during unwarp
        use_syn : bool
            **Experimental**: Enable ANTs SyN-based susceptibility distortion correction (SDC).
            If fieldmaps are present and enabled, this is not run, by default.
        force_syn : bool
            **Temporary**: Always run SyN-based SDC
        output_grid_ref : str or None
            Path of custom reference image for normalization
        use_aroma : bool
            Perform ICA-AROMA on MNI-resampled functional series
        ignore_aroma_err : bool
            Do not fail on ICA-AROMA errors

    Inputs

        subjects_dir
            FreeSurfer SUBJECTS_DIR

    """
    if name in ('single_subject_wf', 'single_subject_fmripreptest_wf'):
        # for documentation purposes
        subject_data = {
            't1w': ['/completely/made/up/path/sub-01_T1w.nii.gz'],
            'bold': ['/completely/made/up/path/sub-01_task-nback_bold.nii.gz']
        }
        layout = None
    else:
        subject_data, layout = collect_data(bids_dir, subject_id, task_id)

    # Make sure we always go through these two checks
    if not anat_only and subject_data['bold'] == []:
        raise Exception("No BOLD images found for participant {} and task {}. "
                        "All workflows require BOLD images.".format(
                            subject_id, task_id if task_id else '<all>'))

    if not subject_data['t1w']:
        raise Exception("No T1w images found for participant {}. "
                        "All workflows require T1w images.".format(subject_id))

    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(niu.IdentityInterface(fields=['subjects_dir']),
                        name='inputnode')

    bidssrc = pe.Node(BIDSDataGrabber(subject_data=subject_data, anat_only=anat_only),
                      name='bidssrc')

    bids_info = pe.Node(BIDSInfo(), name='bids_info', run_without_submitting=True)

    summary = pe.Node(SubjectSummary(output_spaces=output_spaces, template=template),
                      name='summary', run_without_submitting=True)

    about = pe.Node(AboutSummary(version=__version__,
                                 command=' '.join(sys.argv)),
                    name='about', run_without_submitting=True)

    ds_summary_report = pe.Node(
        DerivativesDataSink(base_directory=reportlets_dir,
                            suffix='summary'),
        name='ds_summary_report', run_without_submitting=True)

    ds_about_report = pe.Node(
        DerivativesDataSink(base_directory=reportlets_dir,
                            suffix='about'),
        name='ds_about_report', run_without_submitting=True)

    # Preprocessing of T1w (includes registration to MNI)
    anat_preproc_wf = init_anat_preproc_wf(name="anat_preproc_wf",
                                           skull_strip_ants=skull_strip_ants,
                                           output_spaces=output_spaces,
                                           template=template,
                                           debug=debug,
                                           longitudinal=longitudinal,
                                           omp_nthreads=omp_nthreads,
                                           freesurfer=freesurfer,
                                           hires=hires,
                                           reportlets_dir=reportlets_dir,
                                           output_dir=output_dir)

    workflow.connect([
        (inputnode, anat_preproc_wf, [('subjects_dir', 'inputnode.subjects_dir')]),
        (bidssrc, bids_info, [(('t1w', fix_multi_T1w_source_name), 'in_file')]),
        (inputnode, summary, [('subjects_dir', 'subjects_dir')]),
        (bidssrc, summary, [('t1w', 't1w'),
                            ('t2w', 't2w'),
                            ('bold', 'bold')]),
        (bids_info, summary, [('subject_id', 'subject_id')]),
        (bidssrc, anat_preproc_wf, [('t1w', 'inputnode.t1w'),
                                    ('t2w', 'inputnode.t2w')]),
        (summary, anat_preproc_wf, [('subject_id', 'inputnode.subject_id')]),
        (bidssrc, ds_summary_report, [(('t1w', fix_multi_T1w_source_name), 'source_file')]),
        (summary, ds_summary_report, [('out_report', 'in_file')]),
        (bidssrc, ds_about_report, [(('t1w', fix_multi_T1w_source_name), 'source_file')]),
        (about, ds_about_report, [('out_report', 'in_file')]),
    ])

    if anat_only:
        return workflow

    for bold_file in subject_data['bold']:
        func_preproc_wf = init_func_preproc_wf(bold_file=bold_file,
                                               layout=layout,
                                               ignore=ignore,
                                               freesurfer=freesurfer,
                                               bold2t1w_dof=bold2t1w_dof,
                                               reportlets_dir=reportlets_dir,
                                               output_spaces=output_spaces,
                                               template=template,
                                               output_dir=output_dir,
                                               omp_nthreads=omp_nthreads,
                                               low_mem=low_mem,
                                               fmap_bspline=fmap_bspline,
                                               fmap_demean=fmap_demean,
                                               use_syn=use_syn,
                                               force_syn=force_syn,
                                               debug=debug,
                                               output_grid_ref=output_grid_ref,
                                               use_aroma=use_aroma,
                                               ignore_aroma_err=ignore_aroma_err)

        workflow.connect([
            (anat_preproc_wf, func_preproc_wf,
             [('outputnode.t1_preproc', 'inputnode.t1_preproc'),
              ('outputnode.t1_brain', 'inputnode.t1_brain'),
              ('outputnode.t1_mask', 'inputnode.t1_mask'),
              ('outputnode.t1_seg', 'inputnode.t1_seg'),
              ('outputnode.t1_tpms', 'inputnode.t1_tpms'),
              ('outputnode.t1_2_mni_forward_transform', 'inputnode.t1_2_mni_forward_transform'),
              ('outputnode.t1_2_mni_reverse_transform', 'inputnode.t1_2_mni_reverse_transform')])
        ])

        if freesurfer:
            workflow.connect([
                (anat_preproc_wf, func_preproc_wf,
                 [('outputnode.subjects_dir', 'inputnode.subjects_dir'),
                  ('outputnode.subject_id', 'inputnode.subject_id'),
                  ('outputnode.fs_2_t1_transform', 'inputnode.fs_2_t1_transform')]),
            ])

    return workflow
