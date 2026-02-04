describe('Home page', () => {
  it('loads and shows title', () => {
    cy.visit('/')
    cy.get('h1, h2').should('exist')
  })
})
