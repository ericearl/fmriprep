#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""
Interfaces to generate reportlets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


"""

import os
import time
from collections import Counter
from niworkflows.nipype.interfaces.base import (
    traits, TraitedSpec, BaseInterfaceInputSpec,
    File, Directory, InputMultiPath, Str, isdefined)
from niworkflows.interfaces.base import SimpleInterface

from niworkflows.nipype.interfaces import freesurfer as fs

from .bids import BIDS_NAME

SUBJECT_TEMPLATE = """\t<ul class="elem-desc">
\t\t<li>Subject ID: {subject_id}</li>
\t\t<li>Structural images: {n_t1s:d} T1-weighted {t2w}</li>
\t\t<li>Functional series: {n_bold:d}</li>
{tasks}
\t\t<li>Resampling targets: {output_spaces}
\t\t<li>FreeSurfer reconstruction: {freesurfer_status}</li>
\t</ul>
"""

FUNCTIONAL_TEMPLATE = """\t\t<h3 class="elem-title">Summary</h3>
\t\t<ul class="elem-desc">
\t\t\t<li>Slice timing correction: {stc}</li>
\t\t\t<li>Susceptibility distortion correction: {sdc}</li>
\t\t\t<li>Registration: {registration}</li>
\t\t\t<li>Functional series resampled to spaces: {output_spaces}</li>
\t\t\t<li>Confounds collected: {confounds}</li>
\t\t</ul>
"""

ABOUT_TEMPLATE = """\t<ul>
\t\t<li>FMRIPREP version: {version}</li>
\t\t<li>FMRIPREP command: <tt>{command}</tt></li>
\t\t<li>Date preprocessed: {date}</li>
\t</ul>
</div>
"""


class SummaryOutputSpec(TraitedSpec):
    out_report = File(exists=True, desc='HTML segment containing summary')


class SummaryInterface(SimpleInterface):
    output_spec = SummaryOutputSpec

    def _run_interface(self, runtime):
        segment = self._generate_segment()
        fname = os.path.abspath('report.html')
        with open(fname, 'w') as fobj:
            fobj.write(segment)

        self._results['out_report'] = fname

        return runtime


class SubjectSummaryInputSpec(BaseInterfaceInputSpec):
    t1w = InputMultiPath(File(exists=True), desc='T1w structural images')
    t2w = InputMultiPath(File(exists=True), desc='T2w structural images')
    subjects_dir = Directory(desc='FreeSurfer subjects directory')
    subject_id = Str(desc='Subject ID')
    bold = InputMultiPath(File(exists=True), desc='BOLD functional series')
    output_spaces = traits.List(desc='Target spaces')
    template = traits.Enum('MNI152NLin2009cAsym', desc='Template space')


class SubjectSummaryOutputSpec(SummaryOutputSpec):
    # This exists to ensure that the summary is run prior to the first ReconAll
    # call, allowing a determination whether there is a pre-existing directory
    subject_id = Str(desc='FreeSurfer subject ID')


class SubjectSummary(SummaryInterface):
    input_spec = SubjectSummaryInputSpec
    output_spec = SubjectSummaryOutputSpec

    def _run_interface(self, runtime):
        if isdefined(self.inputs.subject_id):
            self._results['subject_id'] = self.inputs.subject_id
        return super(SubjectSummary, self)._run_interface(runtime)

    def _generate_segment(self):
        if not isdefined(self.inputs.subjects_dir):
            freesurfer_status = 'Not run'
        else:
            recon = fs.ReconAll(subjects_dir=self.inputs.subjects_dir,
                                subject_id=self.inputs.subject_id,
                                T1_files=self.inputs.t1w,
                                flags='-noskullstrip')
            if recon.cmdline.startswith('echo'):
                freesurfer_status = 'Pre-existing directory'
            else:
                freesurfer_status = 'Run by FMRIPREP'

        output_spaces = [self.inputs.template if space == 'template' else space
                         for space in self.inputs.output_spaces]

        t2w_seg = ''
        if self.inputs.t2w:
            t2w_seg = '(+ {:d} T2-weighted)'.format(len(self.inputs.t2w))

        # Add list of tasks with number of runs
        bold_series = self.inputs.bold if isdefined(self.inputs.bold) else []
        counts = Counter(BIDS_NAME.search(series).groupdict()['task_id'][5:]
                         for series in bold_series)
        tasks = ''
        if counts:
            header = '\t\t<ul class="elem-desc">'
            footer = '\t\t</ul>'
            lines = ['\t\t\t<li>Task: {task_id} ({n_runs:d} run{s})</li>'.format(
                         task_id=task_id, n_runs=n_runs, s='' if n_runs == 1 else 's')
                     for task_id, n_runs in sorted(counts.items())]
            tasks = '\n'.join([header] + lines + [footer])

        return SUBJECT_TEMPLATE.format(subject_id=self.inputs.subject_id,
                                       n_t1s=len(self.inputs.t1w),
                                       t2w=t2w_seg,
                                       n_bold=len(bold_series),
                                       tasks=tasks,
                                       output_spaces=', '.join(output_spaces),
                                       freesurfer_status=freesurfer_status)


class FunctionalSummaryInputSpec(BaseInterfaceInputSpec):
    slice_timing = traits.Bool(False, usedefault=True, desc='Slice timing correction used')
    distortion_correction = traits.Enum('epi', 'fieldmap', 'phasediff', 'SyN', 'None',
                                        desc='Susceptibility distortion correction method',
                                        mandatory=True)
    registration = traits.Enum('FLIRT', 'bbregister', mandatory=True,
                               desc='Functional/anatomical registration method')
    output_spaces = traits.List(desc='Target spaces')
    confounds = traits.List(desc='Confounds collected')


class FunctionalSummary(SummaryInterface):
    input_spec = FunctionalSummaryInputSpec

    def _generate_segment(self):
        stc = "Applied" if self.inputs.slice_timing else "Not applied"
        sdc = {'epi': 'Phase-encoding polarity (pepolar)',
               'fieldmap': 'Direct fieldmapping',
               'phasediff': 'Phase difference',
               'SyN': 'Symmetric normalization (SyN) - no fieldmaps',
               'None': 'None'}[self.inputs.distortion_correction]
        reg = {'FLIRT': 'FLIRT with boundary-based registration (BBR) metric',
               'bbregister': 'FreeSurfer boundary-based registration (bbregister)'
               }[self.inputs.registration]
        return FUNCTIONAL_TEMPLATE.format(stc=stc, sdc=sdc, registration=reg,
                                          output_spaces=', '.join(self.inputs.output_spaces),
                                          confounds=', '.join(self.inputs.confounds))


class AboutSummaryInputSpec(BaseInterfaceInputSpec):
    version = Str(desc='FMRIPREP version')
    command = Str(desc='FMRIPREP command')
    # Date not included - update timestamp only if version or command changes


class AboutSummary(SummaryInterface):
    input_spec = AboutSummaryInputSpec

    def _generate_segment(self):
        return ABOUT_TEMPLATE.format(version=self.inputs.version,
                                     command=self.inputs.command,
                                     date=time.strftime("%Y-%m-%d %H:%M:%S %z"))
