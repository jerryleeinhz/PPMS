# Quantum Design PPMS/ETO reference manuals

Downloaded and verified on 2026-08-13. These documents remain copyrighted by
Quantum Design and are stored here only as laboratory reference copies; this
repository does not relicense them. The PDF files are intentionally ignored by
Git so that a local download is not automatically redistributed by the public
repository.

| Local file | Document | Public source | Pages | SHA-256 |
| --- | --- | --- | ---: | --- |
| `PPMS_ETO_User_Manual_1084-700_Rev_B2.pdf` | Electrical Transport Option (ETO) User's Manual, part 1084-700, Rev. B2, December 2017 | [Western Michigan University PPMS manuals](https://wmich.edu/physics/ppmslabmanuals) | 65 | `2DF94DAD338C9C250FAF8DB60B97FBC88C577302C01DC82E79CF095596474436` |
| `PPMS_MultiVu_User_Manual_1070-110_Rev_A2.pdf` | PPMS MultiVu Application User's Manual, part 1070-110, Rev. A2, February 2008 | [NJIT public PPMS documents](https://web.njit.edu/~tyson/PPMS_Documents/PPMS_Manual/1070-110%20A2%20.pdf%20PQ%20MVu.pdf) | 138 | `49909904F22F66B8AF3A71D7546ABCC4067910CA6D62571932617DD9B95FF3EE` |

The ETO manual is the primary reference for wiring, ETO console settings, data
files, measurement modes, and ETO sequence commands. In particular, Chapter 4
covers software and data files, while Section 5.5 introduces sequence
measurements.

The MultiVu manual is the primary reference for the sequence editor and normal
operator workflow. Chapter 5 covers creating, running, pausing, resuming, and
aborting sequences; Chapter 6 covers the built-in sequence commands.

## Availability boundary

Quantum Design publicly describes ETO and links current product literature on
its [DynaCool/PPMS product page](https://qdusa.com/products/dynacool.html).
Quantum Design's [software upgrade page](https://qdusa.com/support/software_upgrades.html)
states that current MultiVu software and user manuals require a Pharos account.
The two files above are complete older revisions made publicly downloadable by
university instrument facilities.

Neither downloaded manual documents a supported OLE/COM programming interface
for loading, starting, stopping, or querying a sequence from an external Python
process. Therefore they help us interpret ETO and sequence behavior, but they do
not replace the read-only `inspect-multivu-ole` JSON collected from the actual
PPMS computer and installed MultiVu version.
