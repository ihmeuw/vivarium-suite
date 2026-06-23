def call(Map config = [:]) {

  // Add team members as necessary
  def mapping = [
    "Jim Albright": "albrja",
    "Steve Bachmeier": "sbachmei",
    "Hussain Jafari": "hjafari",
    "Patrick Nast": "pnast",
    "Rajan Mudambi": "rmudambi",
  ]
  return mapping.get(config.github_author, "channel")
}