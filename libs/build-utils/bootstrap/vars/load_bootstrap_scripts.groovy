def call() {
    writeFile file: 'get_vbu_version.py', text: libraryResource('scripts/get_vbu_version.py')
}