# Source TDMS data layout

Raw and LabVIEW ANAL TDMS files are immutable source data. Keep each pair in
the following canonical layout while preserving both original filenames.

```text
data/source_tdms/
├─ _incoming/
└─ samples/
   └─ <sample_name>/
      └─ N<measurement_number>/
         ├─ raw/
         │  └─ <original RAW filename>.tdms
         └─ anal/
            └─ <original ANAL filename>.tdms
```

Example:

```text
data/source_tdms/samples/oxytocin/N001/raw/<RAW file>.tdms
data/source_tdms/samples/oxytocin/N001/anal/<ANAL file>.tdms
```

## Naming

- `sample_name`: classification label such as `oxytocin`, `AOMe`, or
  `HPSPAA01`.
- `measurement_number`: zero-padded number from the acquisition filename,
  such as `001`.
- `pair_id`: `<sample_name>-N<measurement_number>`.
- Original TDMS filenames must not be renamed.

Concentration, acquisition date, device, gap, and other experimental
conditions belong in the catalog and manifests rather than directory names.

## Ingestion

1. Copy a new RAW/ANAL pair into `data/source_tdms/_incoming/`.
2. Confirm that both files can be read and that their waveform hashes match.
3. Move the verified pair into the canonical sample/measurement directory.
4. Regenerate the pair manifest.

```powershell
python scripts/build_pair_manifest.py `
  data/source_tdms/samples `
  data/manifests/validation_pairs.csv
```

Manifest paths are stored relative to the manifest file. Validation therefore
works independently of the current working directory.

```powershell
python scripts/run_batch_validation.py `
  data/manifests/validation_pairs.csv `
  --noise-method blockwise `
  --noise-block-points 10000 `
  --threshold-k1 3.0 `
  --threshold-k2 5.5
```

Build searchable event tables and compressed waveform regions with the same
manifest and detector settings:

```powershell
python scripts/build_event_datasets.py `
  data/manifests/validation_pairs.csv `
  --noise-method blockwise `
  --noise-block-points 10000 `
  --threshold-k1 3.0 `
  --threshold-k2 5.5 `
  --verify-anal
```

Outputs are separated by waveform and detector configuration:

```text
data/events/
├─ dataset_index.csv
├─ dataset_summary.json
└─ <file_id>/
   └─ <config_hash>/
      ├─ events.csv
      └─ segments/
         └─ <event_id>.npz
```

## Dataset splits

Do not move TDMS files when assigning tuning, validation, or holdout roles.
Keep source files fixed and define splits with versioned manifests under
`data/manifests/splits/`.

TDMS files, generated manifests containing local paths, event arrays, and
validation outputs under `data/` are excluded from Git and require a separate
backup policy.
