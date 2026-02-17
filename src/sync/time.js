class DateTime {
  constructor(date) {
    this.date = date;
  }

  static nowUtc() {
    return new DateTime(new Date());
  }

  minusHours(hours) {
    return new DateTime(new Date(this.date.getTime() - hours * 60 * 60 * 1000));
  }

  plusHours(hours) {
    return new DateTime(new Date(this.date.getTime() + hours * 60 * 60 * 1000));
  }

  toIso() {
    return this.date.toISOString();
  }
}

module.exports = { DateTime };
