def call() {
    writeFile file: 'base.mk', text: libraryResource('makefiles/base.mk')
    writeFile file: 'test.mk', text: libraryResource('makefiles/test.mk')
}