class DenseMapper:
    def __init__( self ):
        self.code_to_int = {}
        self.int_to_code = []
        self.next_code = 0

    def get_int( self, code ) -> int:
        if code in self.code_to_int:
            return self.code_to_int[ code ]
        else:
            i = self.next_code
            self.code_to_int[ code ] = i
            self.int_to_code.append( code )
            self.next_code += 1
            return i
        
    def __len__(self):
        return self.next_code

    def get_code( self, i:int ) -> str:
        return self.int_to_code[ i ]