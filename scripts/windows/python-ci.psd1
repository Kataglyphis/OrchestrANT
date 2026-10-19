# The hub's Invoke-PythonCiBuild.ps1 settings; ExtraPaths and BanditExcludes must equal scripts/linux/ci_static_analysis.sh.
@{
    PackageName        = 'orchestrant'
    FreeThreadedExtras = 'test'
    ExtraPaths         = @('benchmarks', 'frontend', 'bench', 'examples')
    BanditExcludes     = 'tests,.venv,.venv_static_analysis,ExternalLib,third_party,archive,docs/test_results,benchmarks/tests'
    DataLocks          = @('resources/models/models.lock')
}
