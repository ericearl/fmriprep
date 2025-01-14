#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Direct B0 mapping sequences
~~~~~~~~~~~~~~~~~~~~~~~~~~~

When the fieldmap is directly measured with a prescribed sequence (such as
:abbr:`SE (spiral echo)`), we only need to calculate the corresponding B-Spline
coefficients to adapt the fieldmap to the TOPUP tool.
This procedure is described with more detail `here <https://cni.stanford.edu/\
wiki/GE_Processing#Fieldmaps>`_.

This corresponds to the section 8.9.3 --fieldmap image (and one magnitude image)--
of the BIDS specification.

"""

from niworkflows.nipype.pipeline import engine as pe
from niworkflows.nipype.interfaces import utility as niu, fsl, ants
# Note that deman_image imports from nipype
from niworkflows.nipype.workflows.dmri.fsl.utils import demean_image, cleanup_edge_pipeline
from niworkflows.interfaces.masks import BETRPT

from ...interfaces import IntraModalMerge, DerivativesDataSink, FieldEnhance


def init_fmap_wf(reportlets_dir, omp_nthreads, fmap_bspline, name='fmap_wf'):
    """
    Fieldmap workflow - when we have a sequence that directly measures the fieldmap
    we just need to mask it (using the corresponding magnitude image) to remove the
    noise in the surrounding air region, and ensure that units are Hz.

    .. workflow ::
        :graph2use: orig
        :simple_form: yes

        from fmriprep.workflows.fieldmap.fmap import init_fmap_wf
        wf = init_fmap_wf(reportlets_dir='.', omp_nthreads=6,
                          fmap_bspline=False)

    """

    workflow = pe.Workflow(name=name)
    inputnode = pe.Node(niu.IdentityInterface(
        fields=['magnitude', 'fieldmap']), name='inputnode')
    outputnode = pe.Node(niu.IdentityInterface(fields=['fmap', 'fmap_ref', 'fmap_mask']),
                         name='outputnode')

    # Merge input magnitude images
    magmrg = pe.Node(IntraModalMerge(), name='magmrg')
    # Merge input fieldmap images
    fmapmrg = pe.Node(IntraModalMerge(zero_based_avg=False, hmc=False),
                      name='fmapmrg')

    # de-gradient the fields ("bias/illumination artifact")
    n4_correct = pe.Node(ants.N4BiasFieldCorrection(
        dimension=3, copy_header=True, num_threads=omp_nthreads),
        n_procs=omp_nthreads, name='n4_correct')
    bet = pe.Node(BETRPT(generate_report=True, frac=0.6, mask=True),
                  name='bet')
    ds_fmap_mask = pe.Node(DerivativesDataSink(
        base_directory=reportlets_dir, suffix='fmap_mask'),
        name='ds_fmap_mask', run_without_submitting=True)

    workflow.connect([
        (inputnode, magmrg, [('magnitude', 'in_files')]),
        (inputnode, fmapmrg, [('fieldmap', 'in_files')]),
        (magmrg, n4_correct, [('out_file', 'input_image')]),
        (n4_correct, bet, [('output_image', 'in_file')]),
        (bet, outputnode, [('mask_file', 'fmap_mask'),
                           ('out_file', 'fmap_ref')]),
        (inputnode, ds_fmap_mask, [('fieldmap', 'source_file')]),
        (bet, ds_fmap_mask, [('out_report', 'in_file')]),
    ])

    if fmap_bspline:
        # despike_threshold=1.0, mask_erode=1),
        fmapenh = pe.Node(FieldEnhance(
            unwrap=False, despike=False, njobs=omp_nthreads),
            name='fmapenh', mem_gb=4, n_procs=omp_nthreads)

        workflow.connect([
            (bet, fmapenh, [('mask_file', 'in_mask'),
                            ('out_file', 'in_magnitude')]),
            (fmapmrg, fmapenh, [('out_file', 'in_file')]),
            (fmapenh, outputnode, [('out_file', 'fmap')]),
        ])

    else:
        torads = pe.Node(niu.Function(output_names=['out_file', 'cutoff_hz'],
                                      function=_torads), name='torads')
        prelude = pe.Node(fsl.PRELUDE(), name='prelude')
        tohz = pe.Node(niu.Function(function=_tohz), name='tohz')

        denoise = pe.Node(fsl.SpatialFilter(operation='median', kernel_shape='sphere',
                                            kernel_size=3), name='denoise')
        demean = pe.Node(niu.Function(function=demean_image), name='demean')
        cleanup_wf = cleanup_edge_pipeline(name='cleanup_wf')

        applymsk = pe.Node(fsl.ApplyMask(), name='applymsk')

        workflow.connect([
            (bet, prelude, [('mask_file', 'mask_file'),
                            ('out_file', 'magnitude_file')]),
            (fmapmrg, torads, [('out_file', 'in_file')]),
            (torads, tohz, [('cutoff_hz', 'cutoff_hz')]),
            (torads, prelude, [('out_file', 'phase_file')]),
            (prelude, tohz, [('unwrapped_phase_file', 'in_file')]),
            (tohz, denoise, [('out', 'in_file')]),
            (denoise, demean, [('out_file', 'in_file')]),
            (demean, cleanup_wf, [('out', 'inputnode.in_file')]),
            (bet, cleanup_wf, [('mask_file', 'inputnode.in_mask')]),
            (cleanup_wf, applymsk, [('outputnode.out_file', 'in_file')]),
            (bet, applymsk, [('mask_file', 'mask_file')]),
            (applymsk, outputnode, [('out_file', 'fmap')]),
        ])

    return workflow


def _torads(in_file, out_file=None):
    import os
    from math import pi
    import nibabel as nb
    from niworkflows.nipype.utils.filemanip import fname_presuffix

    if out_file is None:
        out_file = fname_presuffix(
            in_file, suffix='_rad', newpath=os.getcwd())

    fmapnii = nb.load(in_file)
    fmapdata = fmapnii.get_data()
    cutoff = max(abs(fmapdata.min()), fmapdata.max())
    fmapdata = fmapdata * (pi / cutoff)
    out_img = nb.Nifti1Image(fmapdata, fmapnii.affine, fmapnii.header)
    out_img.set_data_dtype('float32')
    out_img.to_filename(out_file)
    return out_file, cutoff


def _tohz(in_file, cutoff_hz, out_file=None):
    import os
    from math import pi
    import nibabel as nb
    from niworkflows.nipype.utils.filemanip import fname_presuffix

    if out_file is None:
        out_file = fname_presuffix(in_file, suffix='_hz',
                                   newpath=os.getcwd())

    fmapnii = nb.load(in_file)
    fmapdata = fmapnii.get_data()
    fmapdata = fmapdata * (cutoff_hz / pi)
    out_img = nb.Nifti1Image(fmapdata, fmapnii.affine, fmapnii.header)
    out_img.set_data_dtype('float32')
    out_img.to_filename(out_file)
    return out_file
