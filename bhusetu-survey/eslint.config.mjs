import ts from 'eslint-config-next/typescript';
export default [...ts, { ignores: ['dist/**', '.verification/**'] }];
