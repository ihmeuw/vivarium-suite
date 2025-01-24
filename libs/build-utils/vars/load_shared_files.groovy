def call() {
    writeFile file: 'base.mk', text: libraryResource('makefiles/base.mk')
    writeFile file: 'test.mk', text: libraryResource('makefiles/test.mk')
    writeFile file: 'install_dependency_branch.sh', text: libraryResource('scripts/install_dependency_branch.sh')
}